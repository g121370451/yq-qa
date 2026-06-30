from __future__ import annotations

import asyncio
from typing import AsyncIterator, Protocol

from yq_qa_rag.models import CapabilitiesResponse, ChatRequest, ChatResponse, StreamEvent


class RagAdapter(Protocol):
    name: str

    async def health(self) -> dict:
        ...

    def capabilities(self) -> CapabilitiesResponse:
        ...

    async def chat(self, request: ChatRequest, cancel_event: asyncio.Event) -> ChatResponse:
        ...

    async def stream_chat(
        self, request: ChatRequest, cancel_event: asyncio.Event
    ) -> AsyncIterator[StreamEvent]:
        ...

    async def cancel(self, request_id: str) -> bool:
        ...
