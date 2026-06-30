from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


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
    query: str
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
    question: str
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
