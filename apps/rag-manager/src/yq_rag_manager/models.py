from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


BackendType = Literal["openviking_bot", "deepread"]
RuntimeStatus = Literal["registered", "starting", "running", "stopped", "crashed"]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MethodCreate(BaseModel):
    method_id: str = Field(..., min_length=1)
    backend_type: BackendType
    display_name: str | None = None
    enabled: bool = True
    config: dict[str, Any] = Field(default_factory=dict)


class MethodUpdate(BaseModel):
    display_name: str | None = None
    enabled: bool | None = None
    config: dict[str, Any] | None = None


class RagMethod(BaseModel):
    method_id: str
    backend_type: BackendType
    display_name: str | None = None
    enabled: bool = True
    config: dict[str, Any] = Field(default_factory=dict)
    status: RuntimeStatus = "registered"
    worker_url: str | None = None
    worker_port: int | None = None
    pid: int | None = None
    created_at: datetime
    updated_at: datetime
    last_health: dict[str, Any] | None = None


class MethodList(BaseModel):
    methods: list[RagMethod]
    total: int


class Message(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str


class Source(BaseModel):
    source_id: str
    title: str | None = None
    url: str | None = None
    snippet: str | None = None
    score: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RetrieveRequest(BaseModel):
    request_id: str | None = None
    query: str = Field(..., min_length=1)
    top_k: int = 5
    options: dict[str, Any] = Field(default_factory=dict)

    def request_key(self) -> str:
        return self.request_id or str(uuid4())


class RetrieveResponse(BaseModel):
    request_id: str
    method_id: str
    sources: list[Source] = Field(default_factory=list)
    latency_ms: float | None = None
    backend_metadata: dict[str, Any] = Field(default_factory=dict)


class ChatRequest(BaseModel):
    request_id: str | None = None
    session_id: str | None = None
    user_id: str | None = "default"
    question: str = Field(..., min_length=1)
    history: list[Message] = Field(default_factory=list)
    options: dict[str, Any] = Field(default_factory=dict)

    def request_key(self) -> str:
        return self.request_id or str(uuid4())


class ChatResponse(BaseModel):
    request_id: str
    method_id: str
    session_id: str | None = None
    answer: str
    sources: list[Source] = Field(default_factory=list)
    latency_ms: float | None = None
    backend_metadata: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=utcnow)


class DocumentCreate(BaseModel):
    document_id: str | None = None
    path: str | None = None
    url: str | None = None
    content: str | None = None
    title: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    options: dict[str, Any] = Field(default_factory=dict)


class DocumentResponse(BaseModel):
    document_id: str
    method_id: str
    status: str
    message: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class IngestionJobCreate(BaseModel):
    documents: list[DocumentCreate] = Field(..., min_length=1)
    options: dict[str, Any] = Field(default_factory=dict)
    max_concurrency: int = Field(default=1, ge=1, le=32)
    poll_interval_sec: float = Field(default=2.0, ge=0.5, le=60.0)


class RuntimeResponse(BaseModel):
    method_id: str
    status: RuntimeStatus
    worker_url: str | None = None
    worker_port: int | None = None
    pid: int | None = None
    message: str | None = None


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"] = "ok"
    name: str
    version: str
    timestamp: datetime = Field(default_factory=utcnow)
    details: dict[str, Any] = Field(default_factory=dict)


class StatsResponse(BaseModel):
    total_requests: int = 0
    success_requests: int = 0
    failed_requests: int = 0
    avg_latency_ms: float | None = None
    by_method: dict[str, Any] = Field(default_factory=dict)


class WorkerEventType(str, Enum):
    STATUS = "status"
    RETRIEVAL = "retrieval"
    DELTA = "delta"
    DONE = "done"
    ERROR = "error"
