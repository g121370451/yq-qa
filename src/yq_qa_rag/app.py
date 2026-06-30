from __future__ import annotations

import json
import re
from typing import AsyncIterator
from uuid import uuid4

import httpx
from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse

from yq_qa_rag import __version__
from yq_qa_rag.adapters import DeepReadAdapter, OpenVikingRagAdapter, OVBotAdapter, RagAdapter
from yq_qa_rag.config import AppConfig
from yq_qa_rag.document_runner import DocumentIngestionRunner
from yq_qa_rag.models import (
    CancelResponse,
    CapabilitiesResponse,
    ChatRequest,
    ChatResponse,
    DocumentJobDetail,
    DocumentJobList,
    DocumentJobStatus,
    DocumentUploadCreated,
    EventType,
    HealthResponse,
    QaCancelResponse,
    QaRuntimeConfig,
    QaRuntimeConfigPublic,
    QaRuntimeConfigUpdate,
    QaTaskCreate,
    QaTaskCreated,
    QaTaskDetail,
    QaTaskList,
    StreamEvent,
    TaskEventList,
    TaskStatus,
    UploadedDocument,
)
from yq_qa_rag.registry import RequestRegistry
from yq_qa_rag.rag_manager_client import RagManagerClient
from yq_qa_rag.store import TaskStore
from yq_qa_rag.task_runner import QaTaskRunner


def create_app(config: AppConfig) -> FastAPI:
    adapter = _make_adapter(config)
    registry = RequestRegistry()
    task_store = TaskStore(config.qa_db_path)
    default_runtime_config = _runtime_config_from_app_config(config)
    task_store.ensure_runtime_config(default_runtime_config)
    task_runner = QaTaskRunner(config, task_store)
    document_runner = DocumentIngestionRunner(task_store)

    app = FastAPI(
        title=f"{config.service_name} API",
        description="YQ-QA 问答任务后端。保留单 RAG wrapper 接口，并提供异步 QA task 接口。",
        version=__version__,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    @app.on_event("startup")
    async def startup() -> None:
        task_store.ensure_runtime_config(default_runtime_config)
        task_store.mark_unfinished_failed()

    @app.on_event("shutdown")
    async def shutdown() -> None:
        await document_runner.shutdown()
        await task_runner.shutdown()
        task_store.close()

    @app.get("/health", response_model=HealthResponse, tags=["service"])
    async def health() -> HealthResponse:
        details: dict[str, object] = {}
        status = "ok"
        try:
            upstream = await adapter.health()
            details["legacy_backend"] = upstream
            if upstream.get("status") != "ok":
                status = "degraded"
        except Exception as exc:
            details["legacy_backend"] = {"status": "degraded", "error": str(exc)}
            status = "degraded"
        try:
            runtime_config = task_store.get_runtime_config()
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(f"{runtime_config.rag_manager_base_url}/health")
            details["rag_manager"] = {
                "base_url": runtime_config.rag_manager_base_url,
                "status_code": response.status_code,
                "response": response.json(),
            }
            if response.status_code >= 400:
                status = "degraded"
        except Exception as exc:
            details["rag_manager"] = {"status": "degraded", "error": str(exc)}
            status = "degraded"
        return HealthResponse(
            status=status,
            name=config.service_name,
            backend=config.backend,
            version=__version__,
            details=details,
        )

    @app.get("/capabilities", response_model=CapabilitiesResponse, tags=["service"])
    async def capabilities() -> CapabilitiesResponse:
        return adapter.capabilities()

    @app.get("/v1/config", response_model=QaRuntimeConfigPublic, tags=["config"])
    async def get_runtime_config() -> QaRuntimeConfigPublic:
        return task_store.public_runtime_config(db_path=task_store.db_path)

    @app.put("/v1/config", response_model=QaRuntimeConfigPublic, tags=["config"])
    async def update_runtime_config(
        request: QaRuntimeConfigUpdate,
    ) -> QaRuntimeConfigPublic:
        task_store.update_runtime_config(request)
        return task_store.public_runtime_config(db_path=task_store.db_path)

    @app.get("/v1/rag-methods", tags=["rag-manager"])
    async def list_rag_methods() -> dict:
        try:
            return await RagManagerClient(task_store.get_runtime_config()).list_methods()
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post("/v1/qa/tasks", response_model=QaTaskCreated, tags=["qa"])
    async def create_qa_task(request: QaTaskCreate) -> QaTaskCreated:
        try:
            task_id = task_runner.create_task(request)
            task = task_store.get_task(task_id)
            return QaTaskCreated(
                task_id=task.task_id,
                request_id=task.request_id,
                status=task.status,
                method_ids=task.method_ids,
                merge_strategy=task.merge_strategy,
                created_at=task.created_at,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/v1/qa/tasks", response_model=QaTaskList, tags=["qa"])
    async def list_qa_tasks(
        status: TaskStatus | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=200),
    ) -> QaTaskList:
        tasks = task_store.list_tasks(limit=limit, status=status.value if status else None)
        return QaTaskList(tasks=tasks, total=len(tasks))

    @app.get("/v1/qa/tasks/{task_id}", response_model=QaTaskDetail, tags=["qa"])
    async def get_qa_task(task_id: str) -> QaTaskDetail:
        try:
            return task_store.get_task(task_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="task not found") from exc

    @app.get("/v1/qa/tasks/{task_id}/events", response_model=TaskEventList, tags=["qa"])
    async def list_qa_task_events(
        task_id: str,
        after_id: int = Query(default=0, ge=0),
        limit: int = Query(default=200, ge=1, le=500),
    ) -> TaskEventList:
        try:
            task_store.get_task(task_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="task not found") from exc
        events = task_store.list_events(task_id, after_id=after_id, limit=limit)
        return TaskEventList(events=events, total=len(events))

    @app.get("/v1/qa/tasks/{task_id}/stream", tags=["qa"])
    async def stream_qa_task_events(
        task_id: str,
        after_id: int = Query(default=0, ge=0),
    ) -> StreamingResponse:
        try:
            task_store.get_task(task_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="task not found") from exc

        async def event_generator() -> AsyncIterator[str]:
            cursor = after_id
            while True:
                events = task_store.list_events(task_id, after_id=cursor, limit=100)
                for event in events:
                    cursor = event.event_id
                    yield _raw_sse(event.event_type, event.model_dump(mode="json"))
                task = task_store.get_task(task_id)
                if task.status in {
                    TaskStatus.SUCCEEDED,
                    TaskStatus.FAILED,
                    TaskStatus.CANCELLED,
                }:
                    yield _raw_sse("task", task.model_dump(mode="json"))
                    break
                import asyncio

                await asyncio.sleep(1.0)

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )

    @app.post(
        "/v1/qa/tasks/{task_id}/cancel",
        response_model=QaCancelResponse,
        tags=["qa"],
    )
    async def cancel_qa_task(task_id: str) -> QaCancelResponse:
        try:
            status = task_store.request_cancel(task_id)
            task = task_store.get_task(task_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="task not found") from exc
        cancelled = task.status == TaskStatus.CANCELLED or status in {
            TaskStatus.QUEUED,
            TaskStatus.RUNNING,
        }
        return QaCancelResponse(
            task_id=task_id,
            cancelled=cancelled,
            status=task.status,
            message=(
                "cancel signal recorded"
                if cancelled
                else f"task is already {task.status.value}"
            ),
        )

    @app.post(
        "/v1/documents/upload",
        response_model=DocumentUploadCreated,
        tags=["documents"],
    )
    async def upload_documents(
        method_id: str = Form(...),
        files: list[UploadFile] = File(...),
        metadata_json: str = Form(default="{}"),
        options_json: str = Form(default="{}"),
    ) -> DocumentUploadCreated:
        metadata = _parse_json_object(metadata_json, "metadata_json")
        options = _parse_json_object(options_json, "options_json")
        runtime_config = task_store.get_runtime_config()
        upload_root = _resolve_upload_root(runtime_config.upload_dir)
        batch_id = str(uuid4())
        batch_dir = upload_root / batch_id
        batch_dir.mkdir(parents=True, exist_ok=True)
        documents: list[UploadedDocument] = []
        for index, file in enumerate(files):
            filename = _safe_filename(file.filename or f"document-{index}")
            path = batch_dir / filename
            size = 0
            with path.open("wb") as target:
                while True:
                    chunk = await file.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    target.write(chunk)
            document_id = str(
                metadata.get("document_id")
                or f"{batch_id}-{index}-{path.stem}"
            )
            documents.append(
                UploadedDocument(
                    document_id=document_id,
                    filename=filename,
                    path=str(path.resolve()),
                    title=str(metadata.get("title") or filename),
                    size_bytes=size,
                    metadata={**metadata, "original_filename": file.filename},
                )
            )
        if not documents:
            raise HTTPException(status_code=400, detail="no files uploaded")
        job_id = document_runner.create_job(
            method_id=method_id,
            documents=documents,
            options=options,
        )
        job = task_store.get_document_job(job_id)
        return DocumentUploadCreated(
            job_id=job.job_id,
            method_id=job.method_id,
            status=job.status,
            documents=job.documents,
            created_at=job.created_at,
        )

    @app.get("/v1/documents/ingestion-jobs", response_model=DocumentJobList, tags=["documents"])
    async def list_document_jobs(
        status: DocumentJobStatus | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=200),
    ) -> DocumentJobList:
        jobs = task_store.list_document_jobs(limit=limit, status=status.value if status else None)
        return DocumentJobList(jobs=jobs, total=len(jobs))

    @app.get(
        "/v1/documents/ingestion-jobs/{job_id}",
        response_model=DocumentJobDetail,
        tags=["documents"],
    )
    async def get_document_job(job_id: str) -> DocumentJobDetail:
        try:
            return task_store.get_document_job(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="document job not found") from exc

    @app.get(
        "/v1/documents/ingestion-jobs/{job_id}/events",
        response_model=TaskEventList,
        tags=["documents"],
    )
    async def list_document_job_events(
        job_id: str,
        after_id: int = Query(default=0, ge=0),
        limit: int = Query(default=200, ge=1, le=500),
    ) -> TaskEventList:
        try:
            task_store.get_document_job(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="document job not found") from exc
        events = task_store.list_document_events(job_id, after_id=after_id, limit=limit)
        return TaskEventList(events=events, total=len(events))

    @app.get("/v1/documents/ingestion-jobs/{job_id}/stream", tags=["documents"])
    async def stream_document_job_events(
        job_id: str,
        after_id: int = Query(default=0, ge=0),
    ) -> StreamingResponse:
        try:
            task_store.get_document_job(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="document job not found") from exc

        async def event_generator() -> AsyncIterator[str]:
            cursor = after_id
            while True:
                events = task_store.list_document_events(job_id, after_id=cursor, limit=100)
                for event in events:
                    cursor = event.event_id
                    yield _raw_sse(event.event_type, event.model_dump(mode="json"))
                job = task_store.get_document_job(job_id)
                if job.status in {
                    DocumentJobStatus.SUCCEEDED,
                    DocumentJobStatus.FAILED,
                    DocumentJobStatus.CANCELLED,
                }:
                    yield _raw_sse("job", job.model_dump(mode="json"))
                    break
                import asyncio

                await asyncio.sleep(1.0)

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )

    @app.post(
        "/v1/documents/ingestion-jobs/{job_id}/cancel",
        response_model=DocumentJobDetail,
        tags=["documents"],
    )
    async def cancel_document_job(job_id: str) -> DocumentJobDetail:
        try:
            task_store.request_document_job_cancel(job_id)
            return task_store.get_document_job(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="document job not found") from exc

    @app.post("/v1/chat", response_model=ChatResponse, tags=["chat"])
    async def chat(request: ChatRequest) -> ChatResponse:
        request_id = request.request_key()
        session_id = request.session_key()
        cancel_event = await registry.start(request_id)
        try:
            request.request_id = request_id
            request.session_id = session_id
            return await adapter.chat(request, cancel_event)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        finally:
            await registry.finish(request_id)

    @app.post("/v1/chat/stream", tags=["chat"])
    async def chat_stream(request: ChatRequest) -> StreamingResponse:
        request_id = request.request_key()
        session_id = request.session_key()
        cancel_event = await registry.start(request_id)
        request.request_id = request_id
        request.session_id = session_id

        async def event_generator() -> AsyncIterator[str]:
            try:
                async for event in adapter.stream_chat(request, cancel_event):
                    yield _sse(event)
            except Exception as exc:
                yield _sse(
                    StreamEvent(
                        event=EventType.ERROR,
                        data={"code": "RAG_ERROR", "message": str(exc)},
                    )
                )
            finally:
                await registry.finish(request_id)

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )

    @app.post(
        "/v1/requests/{request_id}/cancel",
        response_model=CancelResponse,
        tags=["requests"],
    )
    async def cancel_request(request_id: str) -> CancelResponse:
        local_cancelled = await registry.cancel(request_id)
        upstream_cancelled = await adapter.cancel(request_id)
        cancelled = local_cancelled or upstream_cancelled
        return CancelResponse(
            request_id=request_id,
            cancelled=cancelled,
            message="cancel signal sent" if cancelled else "request is not active",
        )

    return app


def _make_adapter(config: AppConfig) -> RagAdapter:
    if config.backend == "openviking_rag":
        return OpenVikingRagAdapter(config)
    if config.backend == "ovbot":
        return OVBotAdapter(config)
    if config.backend == "deepread":
        return DeepReadAdapter(config)
    raise ValueError(f"unsupported backend: {config.backend}")


def _sse(event: StreamEvent) -> str:
    payload = event.model_dump(mode="json")
    return f"event: {event.event.value}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _raw_sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _runtime_config_from_app_config(config: AppConfig) -> QaRuntimeConfig:
    return QaRuntimeConfig(
        rag_manager_base_url=config.qa_rag_manager_base_url,
        rag_manager_timeout_seconds=config.qa_rag_manager_timeout_seconds,
        default_method_ids=config.qa_default_method_ids,
        max_concurrent_tasks=config.qa_max_concurrent_tasks,
        method_timeout_seconds=config.qa_method_timeout_seconds,
        upload_dir="data/uploads",
        max_concurrent_ingestion_jobs=2,
        merge_enabled=config.qa_merge_enabled,
        merge_base_url=config.qa_merge_base_url,
        merge_api_key=config.qa_merge_api_key or None,
        merge_model=config.qa_merge_model or None,
        merge_timeout_seconds=config.qa_merge_timeout_seconds,
        merge_temperature=config.qa_merge_temperature,
    )


def _parse_json_object(raw: str, field_name: str) -> dict:
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"{field_name} must be valid JSON") from exc
    if not isinstance(value, dict):
        raise HTTPException(status_code=400, detail=f"{field_name} must be a JSON object")
    return value


def _safe_filename(filename: str) -> str:
    name = filename.replace("\\", "/").split("/")[-1].strip()
    name = re.sub(r"[^A-Za-z0-9._ -]+", "_", name)
    return name or "document"


def _resolve_upload_root(value: str):
    from pathlib import Path

    path = Path(value).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    path.mkdir(parents=True, exist_ok=True)
    return path
