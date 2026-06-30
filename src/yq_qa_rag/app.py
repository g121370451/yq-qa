from __future__ import annotations

import json
from typing import AsyncIterator
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse

from yq_qa_rag import __version__
from yq_qa_rag.adapters import DeepReadAdapter, OpenVikingRagAdapter, OVBotAdapter, RagAdapter
from yq_qa_rag.config import AppConfig
from yq_qa_rag.models import (
    CancelResponse,
    CapabilitiesResponse,
    ChatRequest,
    ChatResponse,
    EventType,
    HealthResponse,
    SessionCreateRequest,
    SessionCreateResponse,
    SessionInfo,
    SessionListResponse,
    StreamEvent,
)
from yq_qa_rag.registry import RequestRegistry, SessionStore


def create_app(config: AppConfig) -> FastAPI:
    adapter = _make_adapter(config)
    registry = RequestRegistry()
    sessions = SessionStore()

    app = FastAPI(
        title=f"{config.service_name} API",
        description="统一 RAG 后端接口。可用同一套协议包装 OV-Bot 或 DeepRead。",
        version=__version__,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    @app.get("/health", response_model=HealthResponse, tags=["service"])
    async def health() -> HealthResponse:
        upstream = await adapter.health()
        return HealthResponse(
            status="ok" if upstream.get("status") == "ok" else "degraded",
            name=config.service_name,
            backend=config.backend,
            version=__version__,
        )

    @app.get("/capabilities", response_model=CapabilitiesResponse, tags=["service"])
    async def capabilities() -> CapabilitiesResponse:
        return adapter.capabilities()

    @app.post("/v1/chat", response_model=ChatResponse, tags=["chat"])
    async def chat(request: ChatRequest) -> ChatResponse:
        request_id = request.request_key()
        session_id = request.session_key()
        if sessions.get(session_id) is None:
            sessions.create(session_id, request.user_id, {})
        sessions.touch(session_id)
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
        if sessions.get(session_id) is None:
            sessions.create(session_id, request.user_id, {})
        sessions.touch(session_id)
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

    @app.post("/v1/sessions", response_model=SessionCreateResponse, tags=["sessions"])
    async def create_session(request: SessionCreateRequest) -> SessionCreateResponse:
        session_id = str(uuid4())
        record = sessions.create(session_id, request.user_id, request.metadata)
        return SessionCreateResponse(session_id=session_id, created_at=record["created_at"])

    @app.get("/v1/sessions", response_model=SessionListResponse, tags=["sessions"])
    async def list_sessions() -> SessionListResponse:
        records = [SessionInfo(**record) for record in sessions.list()]
        return SessionListResponse(sessions=records, total=len(records))

    @app.get("/v1/sessions/{session_id}", response_model=SessionInfo, tags=["sessions"])
    async def get_session(session_id: str) -> SessionInfo:
        record = sessions.get(session_id)
        if record is None:
            raise HTTPException(status_code=404, detail="session not found")
        return SessionInfo(**record)

    @app.delete("/v1/sessions/{session_id}", tags=["sessions"])
    async def delete_session(session_id: str) -> dict[str, bool]:
        deleted = sessions.delete(session_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="session not found")
        return {"deleted": True}

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
