from __future__ import annotations

import time
from typing import AsyncIterator

import httpx
from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse

from yq_rag_manager import __version__
from yq_rag_manager.ingestion_runner import IngestionRunner
from yq_rag_manager.models import (
    ChatRequest,
    ChatResponse,
    DocumentCreate,
    DocumentResponse,
    HealthResponse,
    IngestionJobCreate,
    MethodCreate,
    MethodList,
    MethodUpdate,
    RetrieveRequest,
    RetrieveResponse,
    RuntimeResponse,
    StatsResponse,
)
from yq_rag_manager.store import Store
from yq_rag_manager.supervisor import WorkerSupervisor


def create_app(
    db_path: str,
    logs_dir: str = "logs",
    worker_host: str = "127.0.0.1",
    worker_base_port: int = 18100,
) -> FastAPI:
    store = Store(db_path)
    supervisor = WorkerSupervisor(
        store,
        host=worker_host,
        base_port=worker_base_port,
        logs_dir=logs_dir,
    )
    ingestion_runner = IngestionRunner(store)
    app = FastAPI(
        title="YQ RAG Manager API",
        description="统一管理 OpenViking Bot、DeepRead 等 RAG 方法。",
        version=__version__,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    @app.on_event("startup")
    async def startup() -> None:
        await supervisor.startup()
        for job in store.list_ingestion_jobs(limit=200):
            if job["status"] in {"queued", "running"}:
                ingestion_runner.start_job(job["job_id"])

    @app.on_event("shutdown")
    async def shutdown() -> None:
        await ingestion_runner.shutdown()
        await supervisor.shutdown()

    @app.get("/health", response_model=HealthResponse, tags=["service"])
    async def health() -> HealthResponse:
        methods = store.list_methods()
        running = sum(1 for item in methods if item.status == "running")
        return HealthResponse(
            status="ok",
            name="rag-manager",
            version=__version__,
            details={"methods": len(methods), "running": running},
        )

    @app.get("/v1/stats", response_model=StatsResponse, tags=["service"])
    async def stats() -> StatsResponse:
        return StatsResponse(**store.stats())

    @app.get("/v1/rag-methods", response_model=MethodList, tags=["methods"])
    async def list_methods() -> MethodList:
        methods = store.list_methods()
        return MethodList(methods=methods, total=len(methods))

    @app.post("/v1/rag-methods", response_model=dict, tags=["methods"])
    async def create_method(request: MethodCreate) -> dict:
        try:
            method = store.create_method(request)
            return method.model_dump(mode="json")
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/v1/rag-methods/{method_id}", response_model=dict, tags=["methods"])
    async def get_method(method_id: str) -> dict:
        return _method_json(store, method_id)

    @app.patch("/v1/rag-methods/{method_id}", response_model=dict, tags=["methods"])
    async def update_method(method_id: str, request: MethodUpdate) -> dict:
        try:
            return store.update_method(method_id, request).model_dump(mode="json")
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="method not found") from exc

    @app.delete("/v1/rag-methods/{method_id}", tags=["methods"])
    async def delete_method(method_id: str) -> dict[str, bool]:
        try:
            runtime = supervisor.runtime(method_id)
            if runtime.status == "running":
                await supervisor.stop(method_id)
        except KeyError:
            pass
        deleted = store.delete_method(method_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="method not found")
        return {"deleted": True}

    @app.post(
        "/v1/rag-methods/{method_id}/start",
        response_model=RuntimeResponse,
        tags=["runtime"],
    )
    async def start_method(method_id: str) -> RuntimeResponse:
        try:
            return await supervisor.start(method_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="method not found") from exc
        except Exception as exc:
            store.update_status(method_id, "crashed")
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post(
        "/v1/rag-methods/{method_id}/stop",
        response_model=RuntimeResponse,
        tags=["runtime"],
    )
    async def stop_method(method_id: str) -> RuntimeResponse:
        try:
            return await supervisor.stop(method_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="method not found") from exc

    @app.post(
        "/v1/rag-methods/{method_id}/restart",
        response_model=RuntimeResponse,
        tags=["runtime"],
    )
    async def restart_method(method_id: str) -> RuntimeResponse:
        try:
            return await supervisor.restart(method_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="method not found") from exc
        except Exception as exc:
            store.update_status(method_id, "crashed")
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.get(
        "/v1/rag-methods/{method_id}/runtime",
        response_model=RuntimeResponse,
        tags=["runtime"],
    )
    async def runtime(method_id: str) -> RuntimeResponse:
        try:
            return supervisor.runtime(method_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="method not found") from exc

    @app.get("/v1/rag-methods/{method_id}/health", tags=["runtime"])
    async def method_health(method_id: str) -> dict:
        method = _get_method_or_404(store, method_id)
        if not method.worker_url:
            return {"status": "stopped", "method_id": method_id}
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.get(f"{method.worker_url}/health")
                payload = res.json()
                store.update_health(method_id, payload)
                return payload
        except Exception as exc:
            store.update_status(method_id, "crashed")
            payload = {"status": "degraded", "method_id": method_id, "error": str(exc)}
            store.update_health(method_id, payload)
            return payload

    @app.get("/v1/rag-methods/{method_id}/stats", tags=["runtime"])
    async def method_stats(method_id: str) -> dict:
        _get_method_or_404(store, method_id)
        return store.stats(method_id)

    @app.post(
        "/v1/rag-methods/{method_id}/documents",
        response_model=DocumentResponse,
        tags=["documents"],
    )
    async def create_document(method_id: str, request: DocumentCreate) -> DocumentResponse:
        return await _proxy_json(store, method_id, "post", "/documents", request, "documents")

    @app.get("/v1/rag-methods/{method_id}/documents", tags=["documents"])
    async def list_documents(method_id: str) -> dict:
        return await _proxy_raw(store, method_id, "get", "/documents")

    @app.post("/v1/rag-methods/{method_id}/ingestion-jobs", tags=["documents"])
    async def create_ingestion_job(method_id: str, request: IngestionJobCreate) -> dict:
        _get_running_method(store, method_id)
        job = store.create_ingestion_job(
            method_id,
            request.documents,
            options=request.options,
            max_concurrency=request.max_concurrency,
            poll_interval_sec=request.poll_interval_sec,
        )
        ingestion_runner.start_job(job["job_id"])
        return job

    @app.get("/v1/rag-methods/{method_id}/ingestion-jobs", tags=["documents"])
    async def list_ingestion_jobs(
        method_id: str,
        limit: int = Query(default=50, ge=1, le=200),
    ) -> dict:
        _get_method_or_404(store, method_id)
        return {
            "method_id": method_id,
            "jobs": store.list_ingestion_jobs(method_id=method_id, limit=limit),
        }

    @app.get("/v1/rag-methods/{method_id}/ingestion-jobs/{job_id}", tags=["documents"])
    async def get_ingestion_job(method_id: str, job_id: str) -> dict:
        _get_method_or_404(store, method_id)
        try:
            job = store.get_ingestion_job(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="ingestion job not found") from exc
        if job["method_id"] != method_id:
            raise HTTPException(status_code=404, detail="ingestion job not found")
        return job

    @app.post("/v1/rag-methods/{method_id}/ingestion-jobs/{job_id}/resume", tags=["documents"])
    async def resume_ingestion_job(method_id: str, job_id: str) -> dict:
        _get_running_method(store, method_id)
        try:
            job = store.get_ingestion_job(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="ingestion job not found") from exc
        if job["method_id"] != method_id:
            raise HTTPException(status_code=404, detail="ingestion job not found")
        if job["status"] in {"completed", "failed"}:
            return job
        ingestion_runner.start_job(job_id)
        return store.get_ingestion_job(job_id)

    @app.post("/v1/rag-methods/{method_id}/ingestions/wait", tags=["documents"])
    async def wait_ingestions(
        method_id: str,
        payload: dict | None = Body(default=None),
    ) -> dict:
        return await _proxy_raw(
            store,
            method_id,
            "post",
            "/ingestions/wait",
            json=payload or {},
            operation="ingestions.wait",
        )

    @app.get("/v1/rag-methods/{method_id}/ingestions", tags=["documents"])
    async def list_ingestions(
        method_id: str,
        task_type: str | None = Query(default="add_resource"),
        status: str | None = Query(default=None),
        resource_id: str | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=200),
    ) -> dict:
        return await _proxy_raw(
            store,
            method_id,
            "get",
            "/ingestions",
            params={
                "task_type": task_type,
                "status": status,
                "resource_id": resource_id,
                "limit": limit,
            },
            operation="ingestions.list",
        )

    @app.get("/v1/rag-methods/{method_id}/ingestions/status", tags=["documents"])
    async def ingestion_status(method_id: str) -> dict:
        return await _proxy_raw(
            store,
            method_id,
            "get",
            "/ingestions/status",
            operation="ingestions.status",
        )

    @app.get("/v1/rag-methods/{method_id}/ingestions/{task_id}", tags=["documents"])
    async def get_ingestion(method_id: str, task_id: str) -> dict:
        return await _proxy_raw(
            store,
            method_id,
            "get",
            f"/ingestions/{task_id}",
            operation="ingestions.get",
        )

    @app.patch(
        "/v1/rag-methods/{method_id}/documents/{document_id}",
        response_model=DocumentResponse,
        tags=["documents"],
    )
    async def update_document(
        method_id: str, document_id: str, request: DocumentCreate
    ) -> DocumentResponse:
        return await _proxy_json(
            store, method_id, "patch", f"/documents/{document_id}", request, "documents"
        )

    @app.delete("/v1/rag-methods/{method_id}/documents/{document_id}", tags=["documents"])
    async def delete_document(method_id: str, document_id: str) -> dict:
        return await _proxy_raw(store, method_id, "delete", f"/documents/{document_id}")

    @app.post(
        "/v1/rag-methods/{method_id}/retrieve",
        response_model=RetrieveResponse,
        tags=["query"],
    )
    async def retrieve(method_id: str, request: RetrieveRequest) -> RetrieveResponse:
        return await _proxy_json(store, method_id, "post", "/retrieve", request, "retrieve")

    @app.post(
        "/v1/rag-methods/{method_id}/chat",
        response_model=ChatResponse,
        tags=["query"],
    )
    async def chat(method_id: str, request: ChatRequest) -> ChatResponse:
        return await _proxy_json(store, method_id, "post", "/chat", request, "chat")

    @app.post("/v1/rag-methods/{method_id}/chat/stream", tags=["query"])
    async def chat_stream(method_id: str, request: ChatRequest) -> StreamingResponse:
        method = _get_running_method(store, method_id)

        async def generate() -> AsyncIterator[bytes]:
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream(
                    "POST",
                    f"{method.worker_url}/chat/stream",
                    json=request.model_dump(mode="json"),
                ) as response:
                    response.raise_for_status()
                    async for chunk in response.aiter_bytes():
                        yield chunk

        return StreamingResponse(generate(), media_type="text/event-stream")

    return app


def _get_method_or_404(store: Store, method_id: str):
    try:
        return store.get_method(method_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="method not found") from exc


def _get_running_method(store: Store, method_id: str):
    method = _get_method_or_404(store, method_id)
    if method.status != "running" or not method.worker_url:
        raise HTTPException(status_code=409, detail="method worker is not running")
    return method


def _method_json(store: Store, method_id: str) -> dict:
    return _get_method_or_404(store, method_id).model_dump(mode="json")


async def _proxy_json(store: Store, method_id: str, verb: str, path: str, request, operation: str):
    payload = await _proxy_raw(
        store, method_id, verb, path, json=request.model_dump(mode="json"), operation=operation
    )
    return payload


async def _proxy_raw(
    store: Store,
    method_id: str,
    verb: str,
    path: str,
    json: dict | None = None,
    params: dict | None = None,
    operation: str | None = None,
) -> dict:
    method = _get_running_method(store, method_id)
    started = time.perf_counter()
    request_id = json.get("request_id") if isinstance(json, dict) else None
    try:
        async with httpx.AsyncClient(timeout=None) as client:
            response = await client.request(
                verb.upper(),
                f"{method.worker_url}{path}",
                json=json,
                params={key: value for key, value in (params or {}).items() if value is not None},
            )
        latency_ms = (time.perf_counter() - started) * 1000
        if response.status_code >= 400:
            detail = response.text
            store.log_request(method_id, operation or path, request_id, False, latency_ms, detail)
            raise HTTPException(status_code=response.status_code, detail=detail)
        payload = response.json()
        store.log_request(method_id, operation or path, request_id, True, latency_ms)
        return payload
    except HTTPException:
        raise
    except Exception as exc:
        latency_ms = (time.perf_counter() - started) * 1000
        store.log_request(method_id, operation or path, request_id, False, latency_ms, str(exc))
        raise HTTPException(status_code=502, detail=str(exc)) from exc
