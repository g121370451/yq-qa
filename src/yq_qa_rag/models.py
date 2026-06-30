from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class Message(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str


class Source(BaseModel):
    source_id: str | None = None
    title: str | None = None
    url: str | None = None
    snippet: str | None = None
    score: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChatOptions(BaseModel):
    model_config = ConfigDict(extra="allow")

    top_k: int | None = None
    temperature: float | None = None
    return_sources: bool = True


class ChatRequest(BaseModel):
    request_id: str | None = Field(default=None, description="Client request ID.")
    session_id: str | None = Field(default=None, description="Conversation/session ID.")
    user_id: str | None = Field(default="default", description="Caller user ID.")
    question: str = Field(..., min_length=1)
    history: list[Message] = Field(default_factory=list)
    options: ChatOptions = Field(default_factory=ChatOptions)

    def request_key(self) -> str:
        return self.request_id or str(uuid4())

    def session_key(self) -> str:
        return self.session_id or self.request_key()


class ChatResponse(BaseModel):
    request_id: str
    session_id: str
    answer: str
    sources: list[Source] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class EventType(str, Enum):
    START = "start"
    STATUS = "status"
    RETRIEVAL = "retrieval"
    REASONING = "reasoning"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    DELTA = "delta"
    CITATION = "citation"
    DONE = "done"
    ERROR = "error"


class StreamEvent(BaseModel):
    event: EventType
    data: Any
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"] = "ok"
    name: str
    backend: str
    version: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    details: dict[str, Any] = Field(default_factory=dict)


class CapabilitiesResponse(BaseModel):
    name: str
    backend: str
    stream: bool = True
    citations: bool = True
    sessions: bool = False
    cancel: bool = True
    cancel_mode: Literal["native", "best_effort"] = "best_effort"
    knowledge_manage: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class CancelResponse(BaseModel):
    request_id: str
    cancelled: bool
    message: str


class TaskStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class MergeStrategy(str, Enum):
    AUTO = "auto"
    NONE = "none"
    LLM = "llm"


class QaTaskCreate(BaseModel):
    request_id: str | None = None
    session_id: str | None = None
    user_id: str | None = "default"
    question: str = Field(..., min_length=1)
    history: list[Message] = Field(default_factory=list)
    method_ids: list[str] | None = None
    merge_strategy: MergeStrategy = MergeStrategy.AUTO
    options: dict[str, Any] = Field(default_factory=dict)
    per_method_options: dict[str, dict[str, Any]] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def task_request_id(self) -> str:
        return self.request_id or str(uuid4())


class QaTaskCreated(BaseModel):
    task_id: str
    request_id: str
    status: TaskStatus
    method_ids: list[str]
    merge_strategy: MergeStrategy
    created_at: datetime


class MethodAnswer(BaseModel):
    method_id: str
    status: Literal["succeeded", "failed"]
    answer: str = ""
    sources: list[Source] = Field(default_factory=list)
    latency_ms: float | None = None
    error: str | None = None
    backend_metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class QaTaskSummary(BaseModel):
    task_id: str
    request_id: str
    status: TaskStatus
    question: str
    user_id: str | None = None
    session_id: str | None = None
    method_ids: list[str]
    merge_strategy: MergeStrategy
    merged_answer: str | None = None
    error: str | None = None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class QaTaskDetail(QaTaskSummary):
    history: list[Message] = Field(default_factory=list)
    options: dict[str, Any] = Field(default_factory=dict)
    per_method_options: dict[str, dict[str, Any]] = Field(default_factory=dict)
    results: list[MethodAnswer] = Field(default_factory=list)


class QaTaskList(BaseModel):
    tasks: list[QaTaskSummary]
    total: int


class TaskEvent(BaseModel):
    event_id: int
    task_id: str
    event_type: str
    message: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class TaskEventList(BaseModel):
    events: list[TaskEvent]
    total: int


class QaCancelResponse(BaseModel):
    task_id: str
    cancelled: bool
    status: TaskStatus
    message: str


class QaRuntimeConfig(BaseModel):
    rag_manager_base_url: str = "http://127.0.0.1:18081"
    rag_manager_timeout_seconds: float = 1200.0
    default_method_ids: list[str] = Field(default_factory=list)
    max_concurrent_tasks: int = 4
    method_timeout_seconds: float = 1200.0
    upload_dir: str = "data/uploads"
    max_concurrent_ingestion_jobs: int = 2
    merge_enabled: bool = False
    merge_base_url: str | None = None
    merge_api_key: str | None = None
    merge_model: str | None = None
    merge_timeout_seconds: float = 300.0
    merge_temperature: float = 0.2


class QaRuntimeConfigUpdate(BaseModel):
    rag_manager_base_url: str | None = None
    rag_manager_timeout_seconds: float | None = None
    default_method_ids: list[str] | None = None
    max_concurrent_tasks: int | None = None
    method_timeout_seconds: float | None = None
    upload_dir: str | None = None
    max_concurrent_ingestion_jobs: int | None = None
    merge_enabled: bool | None = None
    merge_base_url: str | None = None
    merge_api_key: str | None = None
    merge_model: str | None = None
    merge_timeout_seconds: float | None = None
    merge_temperature: float | None = None


class QaRuntimeConfigPublic(BaseModel):
    rag_manager_base_url: str
    rag_manager_timeout_seconds: float
    default_method_ids: list[str]
    max_concurrent_tasks: int
    method_timeout_seconds: float
    upload_dir: str
    max_concurrent_ingestion_jobs: int
    merge_enabled: bool
    merge_base_url: str | None = None
    merge_api_key_set: bool = False
    merge_api_key_masked: str | None = None
    merge_model: str | None = None
    merge_timeout_seconds: float
    merge_temperature: float
    db_path: str | None = None


class DocumentJobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class UploadedDocument(BaseModel):
    document_id: str
    filename: str
    path: str
    title: str | None = None
    size_bytes: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class DocumentUploadCreated(BaseModel):
    job_id: str
    method_id: str
    status: DocumentJobStatus
    documents: list[UploadedDocument]
    created_at: datetime


class DocumentJobProgress(BaseModel):
    total_documents: int = 0
    completed_documents: int = 0
    failed_documents: int = 0
    running_documents: int = 0
    pending_documents: int = 0
    progress_percent: float = 0.0
    message: str | None = None


class DocumentJobSummary(BaseModel):
    job_id: str
    method_id: str
    status: DocumentJobStatus
    manager_job_id: str | None = None
    error: str | None = None
    progress: DocumentJobProgress
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None


class DocumentJobDetail(DocumentJobSummary):
    documents: list[UploadedDocument] = Field(default_factory=list)
    options: dict[str, Any] = Field(default_factory=dict)
    manager_response: dict[str, Any] | None = None


class DocumentJobList(BaseModel):
    jobs: list[DocumentJobSummary]
    total: int

