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
    source_id: str
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


class CapabilitiesResponse(BaseModel):
    name: str
    backend: str
    stream: bool = True
    citations: bool = True
    sessions: bool = True
    cancel: bool = True
    cancel_mode: Literal["native", "best_effort"] = "best_effort"
    knowledge_manage: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class CancelResponse(BaseModel):
    request_id: str
    cancelled: bool
    message: str


class SessionCreateRequest(BaseModel):
    user_id: str | None = "default"
    metadata: dict[str, Any] = Field(default_factory=dict)


class SessionInfo(BaseModel):
    session_id: str
    user_id: str | None = None
    created_at: datetime
    last_active: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class SessionListResponse(BaseModel):
    sessions: list[SessionInfo]
    total: int


class SessionCreateResponse(BaseModel):
    session_id: str
    created_at: datetime
