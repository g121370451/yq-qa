from __future__ import annotations

import asyncio
import json
import sys
import threading
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


class DeepReadAdapter:
    name = "deepread"

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self._cache_lock = threading.Lock()
        self._doc_index_cache: dict[tuple[tuple[str, ...], str], Any] = {}

    async def health(self) -> dict:
        try:
            self._ensure_import_path()
            if not self.config.deepread_corpus_paths:
                return {"status": "degraded", "error": "DEEPREAD_CORPUS_PATHS is not set"}
            for path in self.config.deepread_corpus_paths:
                if not Path(path).expanduser().exists():
                    return {"status": "degraded", "error": f"corpus not found: {path}"}
            return {"status": "ok", "corpus_count": len(self.config.deepread_corpus_paths)}
        except Exception as exc:
            return {"status": "degraded", "error": str(exc)}

    def capabilities(self) -> CapabilitiesResponse:
        return CapabilitiesResponse(
            name=self.name,
            backend="deepread",
            stream=True,
            citations=True,
            sessions=False,
            cancel=True,
            cancel_mode="best_effort",
            knowledge_manage=False,
            metadata={
                "project_path": self.config.deepread_project_path,
                "corpus_paths": self.config.deepread_corpus_paths,
                "retrieval": self.config.deepread_retrieval,
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
            data={"request_id": request_id, "session_id": session_id, "backend": "deepread"},
        )
        yield StreamEvent(event=EventType.STATUS, data={"message": "DeepRead agent started"})

        task = asyncio.create_task(self.chat(request, cancel_event))
        while not task.done():
            if cancel_event.is_set():
                yield StreamEvent(
                    event=EventType.ERROR,
                    data={
                        "code": "CANCELLED",
                        "message": "request cancelled; current DeepRead call is best-effort",
                    },
                )
                return
            await asyncio.sleep(0.2)

        response = await task
        if response.sources:
            yield StreamEvent(
                event=EventType.RETRIEVAL,
                data={"sources": [source.model_dump(mode="json") for source in response.sources]},
            )
        yield StreamEvent(event=EventType.DELTA, data={"content": response.answer})
        yield StreamEvent(
            event=EventType.DONE,
            data=response.model_dump(mode="json"),
        )

    async def cancel(self, request_id: str) -> bool:
        return False

    def _run_blocking(self, request: ChatRequest) -> ChatResponse:
        self._ensure_import_path()

        from DeepRead.agent.logger import JsonlLogger
        from DeepRead.agent.runner import run_agent

        request_id = request.request_key()
        session_id = request.session_key()
        corpus_paths = self._option_list(request, "corpus_paths") or self.config.deepread_corpus_paths
        if not corpus_paths:
            raise RuntimeError("DEEPREAD_CORPUS_PATHS is required for DeepRead backend")

        model = self._option_str(request, "model") or self.config.deepread_model
        api_key = self._option_str(request, "api_key") or self.config.deepread_api_key
        base_url = self._option_str(request, "base_url") or self.config.deepread_base_url
        if not model:
            raise RuntimeError("DeepRead model is not configured")
        if not api_key:
            raise RuntimeError("DeepRead API key is not configured")

        log_dir = Path(self.config.deepread_log_dir).expanduser()
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"{request_id}.jsonl"
        logger = JsonlLogger(str(log_path))
        collected_texts: list[str] = []
        doc_index = self._load_doc_index(corpus_paths, self.config.deepread_neighbor_window)

        retrieval = self._option_str(request, "retrieval") or self.config.deepread_retrieval
        enable_vector = self._option_bool(request, "enable_vector", self.config.deepread_enable_vector)
        enable_hybrid = self._option_bool(request, "enable_hybrid", self.config.deepread_enable_hybrid)
        enable_semantic = self._option_bool(
            request, "enable_semantic", self.config.deepread_enable_semantic
        )
        disable_bm25 = self._option_bool(request, "disable_bm25", self.config.deepread_disable_bm25)
        disable_regex = self._option_bool(request, "disable_regex", self.config.deepread_disable_regex)

        if retrieval == "vector":
            enable_vector = True
            disable_bm25 = True
            disable_regex = True
        elif retrieval == "hybrid":
            enable_hybrid = True
        elif retrieval == "semantic":
            enable_semantic = True
        elif retrieval == "bm25":
            disable_bm25 = False

        answer = run_agent(
            model=model,
            base_url=base_url,
            doc_index=doc_index,
            user_question=self._question_with_history(request),
            logger=logger,
            max_rounds=self._option_int(request, "max_rounds", self.config.deepread_max_rounds),
            temperature=request.options.temperature
            if request.options.temperature is not None
            else self.config.deepread_temperature,
            api_key=api_key,
            default_headers=self._openrouter_headers(base_url),
            enable_multimodal=self._option_bool(
                request, "enable_multimodal", self.config.deepread_enable_multimodal
            ),
            enable_vector=enable_vector,
            enable_hybrid=enable_hybrid,
            enable_semantic=enable_semantic,
            disable_bm25=disable_bm25,
            disable_regex=disable_regex,
            disable_read=self._option_bool(request, "disable_read", self.config.deepread_disable_read),
            embed_api_key=self._option_str(request, "embed_api_key")
            or self.config.deepread_embed_api_key,
            embed_base_url=self._option_str(request, "embed_base_url")
            or self.config.deepread_embed_base_url,
            embedding_model=self._option_str(request, "embedding_model")
            or self.config.deepread_embedding_model,
            neighbor_window=self._parse_neighbor_window(self.config.deepread_neighbor_window),
            bm25_topk=self._option_int(request, "bm25_topk", self.config.deepread_bm25_topk),
            regex_topk=self._option_int(request, "regex_topk", self.config.deepread_regex_topk),
            vector_topk=request.options.top_k or self.config.deepread_vector_topk,
            hybrid_topk=request.options.top_k or self.config.deepread_hybrid_topk,
            hybrid_topk_bm25=self._option_int(
                request, "hybrid_topk_bm25", self.config.deepread_hybrid_topk_bm25
            ),
            hybrid_topk_vec=self._option_int(
                request, "hybrid_topk_vec", self.config.deepread_hybrid_topk_vec
            ),
            hybrid_bm25_weight=self._option_float(
                request, "hybrid_bm25_weight", self.config.deepread_hybrid_bm25_weight
            ),
            hybrid_vector_weight=self._option_float(
                request, "hybrid_vector_weight", self.config.deepread_hybrid_vector_weight
            ),
            semantic_stage1_method=self._option_str(request, "semantic_stage1")
            or self.config.deepread_semantic_stage1,
            semantic_topk1=self._option_int(
                request, "semantic_topk1", self.config.deepread_semantic_topk1
            ),
            semantic_topk2=self._option_int(
                request, "semantic_topk2", self.config.deepread_semantic_topk2
            ),
            semantic_stage1_hybrid_topk_bm25=self._option_int(
                request,
                "semantic_stage1_hybrid_topk_bm25",
                self.config.deepread_semantic_stage1_hybrid_topk_bm25,
            ),
            semantic_stage1_hybrid_topk_vec=self._option_int(
                request,
                "semantic_stage1_hybrid_topk_vec",
                self.config.deepread_semantic_stage1_hybrid_topk_vec,
            ),
            rerank_api_key=self._option_str(request, "rerank_api_key")
            or self.config.deepread_rerank_api_key,
            rerank_base_url=self._option_str(request, "rerank_base_url")
            or self.config.deepread_rerank_base_url,
            rerank_model=self._option_str(request, "rerank_model")
            or self.config.deepread_rerank_model,
            tool_fallback=self._option_bool(request, "tool_fallback", self.config.deepread_tool_fallback),
            enable_reasoning=self._option_bool(
                request, "enable_reasoning", self.config.deepread_enable_reasoning
            ),
            collected_texts=collected_texts,
        )

        sources = self._sources_from_log(log_path)
        if not sources:
            sources = self._sources_from_texts(collected_texts)

        return ChatResponse(
            request_id=request_id,
            session_id=session_id,
            answer=answer,
            sources=sources if request.options.return_sources else [],
            metadata={
                "backend": "deepread",
                "log_path": str(log_path),
                "corpus_paths": corpus_paths,
                "retrieval": retrieval,
            },
        )

    def _ensure_import_path(self) -> None:
        project = Path(self.config.deepread_project_path).expanduser().resolve()
        candidates = [project, project / "ov_test", *[Path(p).expanduser() for p in self.config.deepread_extra_pythonpath]]
        for path in candidates:
            text = str(path)
            if path.exists() and text not in sys.path:
                sys.path.insert(0, text)

    def _load_doc_index(self, corpus_paths: list[str], neighbor_window: str) -> Any:
        from DeepRead.tool.corpus import load_corpus

        key = (tuple(str(Path(p).expanduser().resolve()) for p in corpus_paths), neighbor_window)
        with self._cache_lock:
            cached = self._doc_index_cache.get(key)
            if cached is not None:
                return cached
            doc_index = load_corpus(list(key[0]), neighbor_window=self._parse_neighbor_window(neighbor_window))
            self._doc_index_cache[key] = doc_index
            return doc_index

    @staticmethod
    def _parse_neighbor_window(value: str) -> tuple[int, int] | None:
        parts = [p.strip() for p in str(value).split(",")]
        if len(parts) != 2:
            raise ValueError("neighbor window must use 'up,down', for example '1,-1'")
        pair = (int(parts[0]), int(parts[1]))
        if pair == (0, 0):
            return None
        return pair

    @staticmethod
    def _openrouter_headers(base_url: str | None) -> dict[str, str] | None:
        if base_url and "openrouter.ai" in base_url:
            return {"HTTP-Referer": "http://localhost", "X-Title": "yq-qa-deepread"}
        return None

    @staticmethod
    def _question_with_history(request: ChatRequest) -> str:
        if not request.history:
            return request.question
        lines = ["以下是历史对话："]
        for item in request.history:
            if item.role in {"user", "assistant"}:
                lines.append(f"{item.role}: {item.content}")
        lines.append("")
        lines.append(f"当前问题：{request.question}")
        return "\n".join(lines)

    def _sources_from_log(self, log_path: Path) -> list[Source]:
        if not log_path.exists():
            return []

        sources: list[Source] = []
        seen: set[str] = set()
        for line in log_path.read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("event") != "tool_result":
                continue
            result = record.get("result")
            if not isinstance(result, dict):
                continue
            for source in self._sources_from_tool_result(result):
                key = source.source_id + (source.snippet or "")
                if key in seen:
                    continue
                seen.add(key)
                sources.append(source)
                if len(sources) >= 20:
                    return sources
        return sources

    def _sources_from_tool_result(self, result: dict[str, Any]) -> list[Source]:
        out: list[Source] = []
        for item in result.get("results", []) or []:
            if not isinstance(item, dict):
                continue
            ref = item.get("ref") or {}
            source_id = self._source_id(ref)
            out.append(
                Source(
                    source_id=source_id,
                    title=self._title_from_ref(ref),
                    snippet=item.get("text") or item.get("content"),
                    score=self._maybe_float(item.get("score")),
                    metadata={"ref": ref, "tool_result_type": "search"},
                )
            )
            for neighbor in item.get("neighbors", []) or []:
                if isinstance(neighbor, dict) and neighbor.get("text"):
                    out.append(
                        Source(
                            source_id=f"{source_id}:neighbor:{neighbor.get('paragraph_index', '')}",
                            title=self._title_from_ref(ref),
                            snippet=neighbor.get("text"),
                            metadata={"ref": ref, "tool_result_type": "neighbor"},
                        )
                    )

        for idx, paragraph in enumerate(result.get("paragraphs", []) or []):
            if isinstance(paragraph, dict) and paragraph.get("text"):
                ref = {
                    "doc_id": result.get("doc_id"),
                    "node_id": result.get("node_id"),
                    "paragraph_index": paragraph.get("paragraph_index", idx),
                }
                out.append(
                    Source(
                        source_id=self._source_id(ref),
                        title=self._title_from_ref(ref),
                        snippet=paragraph.get("text"),
                        metadata={"ref": ref, "tool_result_type": "read_section"},
                    )
                )
        return out

    @staticmethod
    def _sources_from_texts(texts: list[str]) -> list[Source]:
        sources: list[Source] = []
        seen: set[str] = set()
        for idx, text in enumerate(texts):
            snippet = text.strip()
            if not snippet or snippet in seen:
                continue
            seen.add(snippet)
            sources.append(Source(source_id=f"deepread-text-{idx + 1}", snippet=snippet))
            if len(sources) >= 20:
                break
        return sources

    @staticmethod
    def _source_id(ref: dict[str, Any]) -> str:
        return "deepread:" + ":".join(
            str(ref.get(key, ""))
            for key in ("doc_id", "node_id", "paragraph_index")
            if ref.get(key) is not None
        )

    @staticmethod
    def _title_from_ref(ref: dict[str, Any]) -> str | None:
        doc_id = ref.get("doc_id")
        node_id = ref.get("node_id")
        if doc_id is None and node_id is None:
            return None
        return f"doc={doc_id}, node={node_id}"

    @staticmethod
    def _maybe_float(value: Any) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _extra(request: ChatRequest, name: str, default: Any = None) -> Any:
        return request.options.model_extra.get(name, default)

    def _option_str(self, request: ChatRequest, name: str) -> str | None:
        value = self._extra(request, name)
        return str(value) if value not in (None, "") else None

    def _option_list(self, request: ChatRequest, name: str) -> list[str] | None:
        value = self._extra(request, name)
        if value is None:
            return None
        if isinstance(value, list):
            return [str(item) for item in value if str(item).strip()]
        return [item.strip() for item in str(value).replace(";", ",").split(",") if item.strip()]

    def _option_bool(self, request: ChatRequest, name: str, default: bool) -> bool:
        value = self._extra(request, name, default)
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}

    def _option_int(self, request: ChatRequest, name: str, default: int) -> int:
        value = self._extra(request, name, default)
        return int(value)

    def _option_float(self, request: ChatRequest, name: str, default: float) -> float:
        value = self._extra(request, name, default)
        return float(value)
