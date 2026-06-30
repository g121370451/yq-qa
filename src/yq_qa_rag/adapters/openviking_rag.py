from __future__ import annotations

import asyncio
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, AsyncIterator

from yq_qa_rag.config import AppConfig
from yq_qa_rag.models import (
    CapabilitiesResponse,
    ChatRequest,
    ChatResponse,
    EventType,
    Source,
    StreamEvent,
)


class OpenVikingRagAdapter:
    """Wrapper around OpenViking benchmark/RAG retrieval + generation code."""

    name = "openviking-rag"

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        # Local SyncOpenViking uses a data directory; keep access conservative.
        self._query_lock = threading.Lock()

    async def health(self) -> dict:
        try:
            self._ensure_import_path()
            vector_store = self._vector_store_path()
            if not vector_store:
                return {
                    "status": "degraded",
                    "error": "OPENVIKING_RAG_VECTOR_STORE is not set",
                }
            if self.config.openviking_rag_mode == "local" and not Path(vector_store).exists():
                return {
                    "status": "degraded",
                    "error": f"vector store not found: {vector_store}",
                }
            return {
                "status": "ok",
                "mode": self.config.openviking_rag_mode,
                "vector_store": vector_store,
            }
        except Exception as exc:
            return {"status": "degraded", "error": str(exc)}

    def capabilities(self) -> CapabilitiesResponse:
        return CapabilitiesResponse(
            name=self.name,
            backend="openviking_rag",
            stream=True,
            citations=True,
            sessions=False,
            cancel=True,
            cancel_mode="best_effort",
            knowledge_manage=False,
            metadata={
                "implementation": self.config.openviking_rag_project_path,
                "mode": self.config.openviking_rag_mode,
                "vector_store": self.config.openviking_rag_vector_store,
                "retrieval_topk": self.config.openviking_rag_retrieval_topk,
                "use_relations": self.config.openviking_rag_use_relations,
            },
        )

    async def chat(self, request: ChatRequest, cancel_event: asyncio.Event) -> ChatResponse:
        if cancel_event.is_set():
            raise RuntimeError("request cancelled")
        return await asyncio.to_thread(self._run_blocking, request)

    async def stream_chat(
        self, request: ChatRequest, cancel_event: asyncio.Event
    ) -> AsyncIterator[StreamEvent]:
        request_id = request.request_key()
        session_id = request.session_key()
        yield StreamEvent(
            event=EventType.START,
            data={
                "request_id": request_id,
                "session_id": session_id,
                "backend": "openviking_rag",
            },
        )
        yield StreamEvent(event=EventType.STATUS, data={"message": "OpenViking RAG started"})

        task = asyncio.create_task(self.chat(request, cancel_event))
        while not task.done():
            if cancel_event.is_set():
                yield StreamEvent(
                    event=EventType.ERROR,
                    data={
                        "code": "CANCELLED",
                        "message": "request cancelled; OpenViking RAG call is best-effort",
                    },
                )
                return
            await asyncio.sleep(0.2)

        response = await task
        yield StreamEvent(
            event=EventType.RETRIEVAL,
            data={"sources": [source.model_dump(mode="json") for source in response.sources]},
        )
        yield StreamEvent(event=EventType.DELTA, data={"content": response.answer})
        yield StreamEvent(event=EventType.DONE, data=response.model_dump(mode="json"))

    async def cancel(self, request_id: str) -> bool:
        return False

    def _run_blocking(self, request: ChatRequest) -> ChatResponse:
        self._ensure_import_path()

        from src.core.llm_client import LLMClientWrapper

        request_id = request.request_key()
        session_id = request.session_key()
        vector_store_path = self._vector_store_path()
        if not vector_store_path:
            raise RuntimeError("OPENVIKING_RAG_VECTOR_STORE is required")

        model = self._option_str(request, "model") or self.config.openviking_rag_llm_model
        api_key = self._option_str(request, "api_key") or self.config.openviking_rag_llm_api_key
        base_url = (
            self._option_str(request, "base_url") or self.config.openviking_rag_llm_base_url
        )
        if not model:
            raise RuntimeError("OpenViking RAG LLM model is not configured")
        if not api_key:
            raise RuntimeError("OpenViking RAG LLM API key is not configured")

        llm = LLMClientWrapper(
            config={
                "model": model,
                "temperature": request.options.temperature
                if request.options.temperature is not None
                else self.config.openviking_rag_llm_temperature,
                "base_url": base_url,
            },
            api_key=api_key,
        )

        topk = request.options.top_k or self.config.openviking_rag_retrieval_topk
        retrieval_instruction = (
            self._option_str(request, "retrieval_instruction")
            or self.config.openviking_rag_retrieval_instruction
        )
        query = f"{retrieval_instruction} {request.question}" if retrieval_instruction else request.question

        with self._query_lock:
            store = self._make_vector_store(vector_store_path, llm)
            try:
                t0 = time.time()
                search_res = store.retrieve(
                    query=query,
                    topk=topk,
                    target_uri=self.config.openviking_rag_target_uri,
                )
                retrieval_sec = time.time() - t0
                prompt = self._build_prompt(request.question, search_res.get("context_blocks", []))
                answer = llm.generate(prompt).strip()
                input_tokens = store.count_tokens(prompt)
                output_tokens = store.count_tokens(answer)
            finally:
                close = getattr(store, "close", None)
                if callable(close):
                    close()

        sources = self._sources_from_search(search_res)
        return ChatResponse(
            request_id=request_id,
            session_id=session_id,
            answer=answer,
            sources=sources if request.options.return_sources else [],
            metadata={
                "backend": "openviking_rag",
                "mode": self.config.openviking_rag_mode,
                "retrieval_latency_sec": retrieval_sec,
                "retrieval_tokens": search_res.get("retrieval_tokens", 0),
                "token_usage": {
                    "prompt_tokens": input_tokens,
                    "completion_tokens": output_tokens,
                    "total_tokens": input_tokens + output_tokens,
                },
                "retrieved_uris": search_res.get("retrieved_uris", []),
                "relations_uris": search_res.get("relations_uris", []),
                "relations_found": search_res.get("relations_found", 0),
                "relations_added": search_res.get("relations_added", 0),
            },
        )

    def _make_vector_store(self, vector_store_path: str, llm: Any) -> Any:
        mode = self.config.openviking_rag_mode.strip().lower()

        if mode == "http":
            if self.config.openviking_rag_use_relations:
                from src.core.vector_store_with_relations import VikingStoreHTTPWithRelations

                return VikingStoreHTTPWithRelations(
                    server_url=self.config.openviking_rag_server_url,
                    api_key=self.config.openviking_rag_api_key,
                    store_path=vector_store_path,
                    embedder=self._make_embedder(),
                    strategy=self.config.openviking_rag_link_strategy,
                )

            from src.core.vector_store import VikingStoreHTTPWrapper

            return VikingStoreHTTPWrapper(
                server_url=self.config.openviking_rag_server_url,
                api_key=self.config.openviking_rag_api_key,
            )

        if self.config.openviking_rag_use_relations:
            from src.core.vector_store_with_relations import VikingStoreWithRelations

            return VikingStoreWithRelations(
                store_path=vector_store_path,
                relations_topk=self.config.openviking_rag_relations_topk,
                use_query_expansion=self.config.openviking_rag_use_query_expansion,
                llm=llm.llm if self.config.openviking_rag_use_query_expansion else None,
                embedder=self._make_embedder(),
                strategy=self.config.openviking_rag_link_strategy,
            )

        from src.core.vector_store import VikingStoreWrapper

        return VikingStoreWrapper(store_path=vector_store_path)

    def _make_embedder(self) -> Any | None:
        if not self.config.openviking_rag_embedding_api_key:
            return None
        try:
            from src.core.embedder import VolcengineEmbedder

            return VolcengineEmbedder(
                api_key=self.config.openviking_rag_embedding_api_key,
                base_url=self.config.openviking_rag_embedding_base_url,
                model=self.config.openviking_rag_embedding_model,
            )
        except Exception:
            return None

    def _ensure_import_path(self) -> None:
        rag_root = Path(self.config.openviking_rag_project_path).expanduser().resolve()
        openviking_root = Path(self.config.openviking_root_path).expanduser().resolve()
        candidates = [rag_root, rag_root / "src", openviking_root]
        for path in candidates:
            text = str(path)
            if path.exists() and text not in sys.path:
                sys.path.insert(0, text)

        ov_conf = Path(self.config.openviking_rag_ov_conf).expanduser()
        if ov_conf.exists():
            os.environ["OPENVIKING_CONFIG_FILE"] = str(ov_conf.resolve())

    def _vector_store_path(self) -> str:
        value = self.config.openviking_rag_vector_store
        if not value:
            return ""
        path = Path(value).expanduser()
        if path.is_absolute():
            return str(path)
        return str((Path(self.config.openviking_rag_project_path) / path).resolve())

    @staticmethod
    def _build_prompt(question: str, context_blocks: list[str]) -> str:
        context = "\n\n".join(
            f"[{idx + 1}]\n{block}" for idx, block in enumerate(context_blocks)
        )
        return (
            "Answer the question strictly based on the provided context. "
            "If the context is insufficient, say \"Not mentioned\".\n\n"
            f"Context:\n{context}\n\n"
            f"Question: {question}\n\n"
            "Answer:"
        )

    @staticmethod
    def _sources_from_search(search_res: dict[str, Any]) -> list[Source]:
        recall_texts = search_res.get("recall_texts", {}) or {}
        uris = list(search_res.get("retrieved_uris", []) or [])
        uris.extend(uri for uri in search_res.get("relations_uris", []) or [] if uri not in uris)

        sources: list[Source] = []
        for idx, uri in enumerate(uris):
            content = str(recall_texts.get(uri, ""))
            sources.append(
                Source(
                    source_id=uri or f"openviking-{idx + 1}",
                    title=uri,
                    url=uri,
                    snippet=content[:1000] if content else None,
                    metadata={"uri": uri},
                )
            )
        return sources

    @staticmethod
    def _extra(request: ChatRequest, name: str, default: Any = None) -> Any:
        return request.options.model_extra.get(name, default)

    def _option_str(self, request: ChatRequest, name: str) -> str | None:
        value = self._extra(request, name)
        return str(value) if value not in (None, "") else None
