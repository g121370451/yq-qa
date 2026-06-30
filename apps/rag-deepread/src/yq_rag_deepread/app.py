from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, AsyncIterator
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse

from yq_rag_deepread import __version__
from yq_rag_deepread.config import configure_paths
from yq_rag_deepread.models import (
    ChatRequest,
    ChatResponse,
    DocumentCreate,
    DocumentResponse,
    RetrieveRequest,
    RetrieveResponse,
    Source,
)


class DeepReadWorker:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.method_id = str(config.get("method_id") or "deepread")
        configure_paths(config)
        self._doc_index_cache: dict[tuple[tuple[str, ...], str], Any] = {}
        self._documents: dict[str, str] = {}
        for path in self.corpus_paths:
            self._documents[Path(path).stem] = path

    @property
    def corpus_paths(self) -> list[str]:
        paths = self.config.get("corpus_paths") or []
        if isinstance(paths, str):
            return [item.strip() for item in paths.replace(";", ",").split(",") if item.strip()]
        return [str(item) for item in paths]

    async def health(self) -> dict[str, Any]:
        missing = [path for path in self.corpus_paths if not Path(path).expanduser().exists()]
        return {
            "status": "degraded" if missing else "ok",
            "method_id": self.method_id,
            "backend": "deepread",
            "version": __version__,
            "corpus_count": len(self.corpus_paths),
            "missing": missing,
        }

    async def create_document(self, request: DocumentCreate) -> DocumentResponse:
        if not request.path:
            raise HTTPException(status_code=400, detail="DeepRead document ingest requires path")
        configure_paths(self.config)
        from DeepRead.index.ingest import parse_document

        output_dir = request.options.get("output") or self.config.get("corpus_output_dir")
        build_embeddings = bool(request.options.get("build_embeddings", False))
        corpus_path = parse_document(
            request.path,
            output=output_dir,
            name=request.title or request.document_id,
            build_embedding_index=build_embeddings,
            embedding_model=request.options.get(
                "embedding_model",
                self.config.get("embedding_model", "Qwen/Qwen3-Embedding-8B"),
            ),
            embedding_batch_size=int(request.options.get("embedding_batch_size", 64)),
            embed_base_url=request.options.get(
                "embed_base_url",
                self.config.get("embed_base_url", "http://127.0.0.1:8756/v1"),
            ),
            embed_api_key=request.options.get(
                "embed_api_key",
                self.config.get("embed_api_key", ""),
            ),
            use_pymupdf=bool(request.options.get("use_pymupdf", self.config.get("use_pymupdf", True))),
            ocr_fallback=bool(request.options.get("ocr_fallback", self.config.get("ocr_fallback", False))),
            min_pdf_text_chars=int(
                request.options.get(
                    "min_pdf_text_chars",
                    self.config.get("min_pdf_text_chars", 20),
                )
            ),
            paddle_vl_rec_backend=request.options.get(
                "paddle_vl_rec_backend",
                self.config.get("paddle_vl_rec_backend", "vllm-server"),
            ),
            paddle_vl_rec_server_url=request.options.get(
                "paddle_vl_rec_server_url",
                self.config.get("paddle_vl_rec_server_url", "http://127.0.0.1:8956/v1"),
            ),
        )
        document_id = request.document_id or Path(corpus_path).stem
        self._documents[document_id] = str(corpus_path)
        self.config["corpus_paths"] = sorted(set([*self.corpus_paths, str(corpus_path)]))
        self._doc_index_cache.clear()
        return DocumentResponse(
            document_id=document_id,
            method_id=self.method_id,
            status="ready",
            message="DeepRead corpus created",
            metadata={"corpus_path": str(corpus_path)},
        )

    async def list_documents(self) -> dict[str, Any]:
        return {
            "method_id": self.method_id,
            "documents": [
                {"document_id": key, "corpus_path": value}
                for key, value in sorted(self._documents.items())
            ],
        }

    async def delete_document(self, document_id: str) -> dict[str, Any]:
        path = self._documents.pop(document_id, None)
        if path is None:
            raise HTTPException(status_code=404, detail="document not found")
        self.config["corpus_paths"] = [item for item in self.corpus_paths if item != path]
        self._doc_index_cache.clear()
        return {"deleted": True, "document_id": document_id}

    async def update_document(
        self, document_id: str, request: DocumentCreate
    ) -> DocumentResponse:
        if document_id not in self._documents:
            raise HTTPException(status_code=404, detail="document not found")
        request.document_id = document_id
        old_path = self._documents.pop(document_id)
        self.config["corpus_paths"] = [item for item in self.corpus_paths if item != old_path]
        response = await self.create_document(request)
        self._doc_index_cache.clear()
        return response

    async def retrieve(self, request: RetrieveRequest) -> RetrieveResponse:
        started = time.perf_counter()
        doc_index = self._load_doc_index(request.options)
        mode = request.options.get("retrieval") or self.config.get("retrieval") or "bm25"
        if mode == "vector":
            raw = doc_index.vector_search(
                request.query,
                top_k=request.top_k,
                embed_api_key=request.options.get("embed_api_key")
                or self.config.get("embed_api_key", ""),
                embed_base_url=request.options.get("embed_base_url")
                or self.config.get("embed_base_url"),
                embed_model=request.options.get("embedding_model")
                or self.config.get("embedding_model"),
            )
        elif mode == "hybrid":
            raw = doc_index.hybrid_search(request.query, top_k=request.top_k)
        elif mode == "semantic":
            raw = doc_index.semantic_retrieval(request.query, top_k2=request.top_k)
        elif mode == "regex":
            raw = doc_index.regex_search(request.query, top_k=request.top_k)
        else:
            raw = doc_index.bm25_search(request.query, top_k=request.top_k)
        return RetrieveResponse(
            request_id=request.request_key(),
            method_id=self.method_id,
            sources=_sources_from_deepread(raw),
            latency_ms=(time.perf_counter() - started) * 1000,
            backend_metadata={"backend": "deepread", "retrieval": mode, "raw": raw},
        )

    async def chat(self, request: ChatRequest) -> ChatResponse:
        started = time.perf_counter()
        configure_paths(self.config)
        from DeepRead.agent.logger import JsonlLogger
        from DeepRead.agent.runner import run_agent

        doc_index = self._load_doc_index(request.options)
        request_id = request.request_key()
        log_dir = Path(str(self.config.get("log_dir", "logs/deepread"))).expanduser()
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"{request_id}.jsonl"
        logger = JsonlLogger(str(log_path))
        collected_texts: list[str] = []
        options = {**self.config, **request.options}

        retrieval = options.get("retrieval")
        enable_vector = bool(options.get("enable_vector", False))
        enable_hybrid = bool(options.get("enable_hybrid", False))
        enable_semantic = bool(options.get("enable_semantic", False))
        disable_bm25 = bool(options.get("disable_bm25", False))
        disable_regex = bool(options.get("disable_regex", False))
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
            model=_required(options, "model"),
            base_url=options.get("base_url"),
            doc_index=doc_index,
            user_question=_question_with_history(request),
            logger=logger,
            max_rounds=int(options.get("max_rounds", 50)),
            temperature=float(options.get("temperature", 0.0)),
            api_key=_required(options, "api_key"),
            default_headers=None,
            enable_multimodal=bool(options.get("enable_multimodal", False)),
            enable_vector=enable_vector,
            enable_hybrid=enable_hybrid,
            enable_semantic=enable_semantic,
            disable_bm25=disable_bm25,
            disable_regex=disable_regex,
            disable_read=bool(options.get("disable_read", False)),
            embed_api_key=options.get("embed_api_key", ""),
            embed_base_url=options.get("embed_base_url", "https://api.siliconflow.cn/v1"),
            embedding_model=options.get("embedding_model", "Qwen/Qwen3-Embedding-8B"),
            neighbor_window=_parse_neighbor_window(options.get("neighbor_window", "1,-1")),
            bm25_topk=int(options.get("bm25_topk", 1)),
            regex_topk=int(options.get("regex_topk", 1)),
            vector_topk=int(options.get("vector_topk", request.options.get("top_k", 1))),
            hybrid_topk=int(options.get("hybrid_topk", request.options.get("top_k", 1))),
            hybrid_topk_bm25=int(options.get("hybrid_topk_bm25", 30)),
            hybrid_topk_vec=int(options.get("hybrid_topk_vec", 30)),
            hybrid_bm25_weight=float(options.get("hybrid_bm25_weight", 0.5)),
            hybrid_vector_weight=float(options.get("hybrid_vector_weight", 0.5)),
            semantic_stage1_method=options.get("semantic_stage1", "vector"),
            semantic_topk1=int(options.get("semantic_topk1", 30)),
            semantic_topk2=int(options.get("semantic_topk2", 2)),
            semantic_stage1_hybrid_topk_bm25=int(
                options.get("semantic_stage1_hybrid_topk_bm25", 30)
            ),
            semantic_stage1_hybrid_topk_vec=int(
                options.get("semantic_stage1_hybrid_topk_vec", 30)
            ),
            rerank_api_key=options.get("rerank_api_key", ""),
            rerank_base_url=options.get("rerank_base_url", "https://api.siliconflow.cn/v1"),
            rerank_model=options.get("rerank_model", "Qwen/Qwen3-Reranker-8B"),
            tool_fallback=bool(options.get("tool_fallback", True)),
            enable_reasoning=bool(options.get("enable_reasoning", True)),
            collected_texts=collected_texts,
        )
        sources = _sources_from_log(log_path) or _sources_from_texts(collected_texts)
        return ChatResponse(
            request_id=request_id,
            method_id=self.method_id,
            session_id=request.session_id,
            answer=answer,
            sources=sources,
            latency_ms=(time.perf_counter() - started) * 1000,
            backend_metadata={
                "backend": "deepread",
                "log_path": str(log_path),
                "corpus_paths": self.corpus_paths,
            },
        )

    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[bytes]:
        yield _sse("status", {"message": "DeepRead started"})
        try:
            response = await self.chat(request)
        except Exception as exc:
            yield _sse("error", {"message": str(exc)})
            return
        yield _sse("delta", {"content": response.answer})
        yield _sse("done", response.model_dump(mode="json"))

    def _load_doc_index(self, options: dict[str, Any]) -> Any:
        configure_paths(self.config)
        from DeepRead.tool.corpus import load_corpus

        corpus_paths = options.get("corpus_paths") or self.corpus_paths
        if not corpus_paths:
            raise HTTPException(status_code=400, detail="DeepRead corpus_paths is empty")
        neighbor_window = str(options.get("neighbor_window", self.config.get("neighbor_window", "1,-1")))
        key = (tuple(str(Path(path).expanduser().resolve()) for path in corpus_paths), neighbor_window)
        if key not in self._doc_index_cache:
            self._doc_index_cache[key] = load_corpus(list(key[0]), _parse_neighbor_window(neighbor_window))
        return self._doc_index_cache[key]


def create_app(config: dict[str, Any]) -> FastAPI:
    worker = DeepReadWorker(config)
    app = FastAPI(title="YQ DeepRead RAG Worker", version=__version__)

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return await worker.health()

    @app.get("/stats")
    async def stats() -> dict[str, Any]:
        return {"method_id": worker.method_id, "backend": "deepread"}

    @app.post("/documents", response_model=DocumentResponse)
    async def documents(request: DocumentCreate) -> DocumentResponse:
        return await worker.create_document(request)

    @app.get("/documents")
    async def list_documents() -> dict[str, Any]:
        return await worker.list_documents()

    @app.patch("/documents/{document_id}", response_model=DocumentResponse)
    async def update_document(
        document_id: str, request: DocumentCreate
    ) -> DocumentResponse:
        return await worker.update_document(document_id, request)

    @app.delete("/documents/{document_id}")
    async def delete_document(document_id: str) -> dict[str, Any]:
        return await worker.delete_document(document_id)

    @app.post("/retrieve", response_model=RetrieveResponse)
    async def retrieve(request: RetrieveRequest) -> RetrieveResponse:
        return await worker.retrieve(request)

    @app.post("/chat", response_model=ChatResponse)
    async def chat(request: ChatRequest) -> ChatResponse:
        return await worker.chat(request)

    @app.post("/chat/stream")
    async def chat_stream(request: ChatRequest) -> StreamingResponse:
        return StreamingResponse(worker.stream_chat(request), media_type="text/event-stream")

    return app


def _required(options: dict[str, Any], key: str) -> str:
    value = options.get(key)
    if value:
        return str(value)
    raise HTTPException(status_code=400, detail=f"DeepRead {key} is required")


def _parse_neighbor_window(value: str | None):
    if value in (None, "", "none", "None"):
        return None
    parts = [part.strip() for part in str(value).split(",")]
    if len(parts) != 2:
        raise HTTPException(status_code=400, detail="neighbor_window must be 'up,down'")
    return int(parts[0]), int(parts[1])


def _question_with_history(request: ChatRequest) -> str:
    if not request.history:
        return request.question
    lines = []
    for message in request.history:
        if message.role in {"user", "assistant"}:
            lines.append(f"{message.role}: {message.content}")
    lines.append(f"user: {request.question}")
    return "\n".join(lines)


def _sources_from_deepread(raw: dict[str, Any]) -> list[Source]:
    items = raw.get("results") or raw.get("items") or raw.get("paragraphs") or []
    if not isinstance(items, list):
        items = []
    sources: list[Source] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        ref = item.get("ref") or {}
        source_id = (
            f"{ref.get('doc_id', '')}:{ref.get('node_id', '')}:{ref.get('paragraph_indexes', index)}"
        )
        sources.append(
            Source(
                source_id=source_id,
                title=item.get("title") or item.get("node_id"),
                snippet=item.get("text") or item.get("content") or str(item)[:500],
                score=_float_or_none(item.get("score")),
                metadata=item,
            )
        )
    return sources


def _sources_from_log(log_path: Path) -> list[Source]:
    if not log_path.exists():
        return []
    sources: list[Source] = []
    for line in log_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if "tool_result" not in line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        sources.append(
            Source(
                source_id=f"log:{len(sources)}",
                snippet=json.dumps(item, ensure_ascii=False)[:500],
                metadata=item,
            )
        )
    return sources[:20]


def _sources_from_texts(texts: list[str]) -> list[Source]:
    return [
        Source(source_id=f"text:{index}", snippet=text[:500], metadata={"text": text})
        for index, text in enumerate(texts[:20])
    ]


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except Exception:
        return None


def _sse(event: str, data: Any) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8")
