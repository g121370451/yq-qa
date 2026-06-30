from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator

import httpx

from yq_qa_rag.config import AppConfig
from yq_qa_rag.models import (
    CapabilitiesResponse,
    ChatRequest,
    ChatResponse,
    EventType,
    Source,
    StreamEvent,
)


class OVBotAdapter:
    name = "ov-bot"

    def __init__(self, config: AppConfig) -> None:
        self.config = config

    async def health(self) -> dict:
        url = self._url("/bot/v1/health")
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url, headers=self._headers())
            if resp.status_code >= 400:
                return {"status": "degraded", "upstream_status": resp.status_code}
            return {"status": "ok", "upstream": resp.json()}
        except Exception as exc:
            return {"status": "degraded", "error": str(exc)}

    def capabilities(self) -> CapabilitiesResponse:
        return CapabilitiesResponse(
            name=self.name,
            backend="ovbot",
            stream=True,
            citations=False,
            sessions=False,
            cancel=True,
            cancel_mode="best_effort",
            knowledge_manage=False,
            metadata={
                "upstream_base_url": self.config.ovbot_base_url,
                "chat_path": self.config.ovbot_chat_path,
                "stream_path": self.config.ovbot_chat_stream_path,
            },
        )

    async def chat(self, request: ChatRequest, cancel_event: asyncio.Event) -> ChatResponse:
        request_id = request.request_key()
        session_id = request.session_key()
        payload = self._payload(request, session_id, stream=False)

        async with httpx.AsyncClient(timeout=self.config.ovbot_timeout_seconds) as client:
            resp = await client.post(
                self._url(self.config.ovbot_chat_path),
                headers=self._headers(),
                json=payload,
            )
        self._raise_for_status(resp)
        body = resp.json()
        answer = body.get("message") or body.get("answer") or ""
        return ChatResponse(
            request_id=request_id,
            session_id=str(body.get("session_id") or session_id),
            answer=answer,
            sources=[],
            metadata={"upstream": "ovbot", "events": body.get("events") or []},
        )

    async def stream_chat(
        self, request: ChatRequest, cancel_event: asyncio.Event
    ) -> AsyncIterator[StreamEvent]:
        request_id = request.request_key()
        session_id = request.session_key()
        payload = self._payload(request, session_id, stream=True)
        final_answer = ""

        yield StreamEvent(
            event=EventType.START,
            data={"request_id": request_id, "session_id": session_id, "backend": "ovbot"},
        )

        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream(
                "POST",
                self._url(self.config.ovbot_chat_stream_path),
                headers=self._headers(),
                json=payload,
            ) as resp:
                if resp.status_code >= 400:
                    raw = await resp.aread()
                    raise RuntimeError(
                        f"OV-Bot HTTP {resp.status_code}: {raw.decode('utf-8', errors='ignore')}"
                    )

                async for _, data in self._iter_sse(resp):
                    if cancel_event.is_set():
                        yield StreamEvent(
                            event=EventType.ERROR,
                            data={"code": "CANCELLED", "message": "request cancelled"},
                        )
                        return
                    if not data:
                        continue
                    try:
                        item = json.loads(data)
                    except json.JSONDecodeError:
                        yield StreamEvent(event=EventType.STATUS, data={"content": data})
                        continue

                    event_name = item.get("event") or item.get("type")
                    event_data = item.get("data")

                    if event_name == "response":
                        final_answer = event_data if isinstance(event_data, str) else json.dumps(
                            event_data, ensure_ascii=False
                        )
                        yield StreamEvent(event=EventType.DELTA, data={"content": final_answer})
                    elif event_name == "reasoning":
                        yield StreamEvent(event=EventType.REASONING, data={"content": event_data})
                    elif event_name == "tool_call":
                        yield StreamEvent(event=EventType.TOOL_CALL, data=event_data)
                    elif event_name == "tool_result":
                        yield StreamEvent(event=EventType.TOOL_RESULT, data=event_data)
                    else:
                        yield StreamEvent(event=EventType.STATUS, data=item)

        yield StreamEvent(
            event=EventType.DONE,
            data={
                "request_id": request_id,
                "session_id": session_id,
                "answer": final_answer,
                "sources": [],
                "metadata": {"upstream": "ovbot"},
            },
        )

    async def cancel(self, request_id: str) -> bool:
        return False

    def _url(self, path: str) -> str:
        base = self.config.ovbot_base_url.rstrip("/")
        clean_path = "/" + path.lstrip("/")
        return f"{base}{clean_path}"

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.config.ovbot_api_key:
            headers["X-API-Key"] = self.config.ovbot_api_key
        return headers

    def _payload(self, request: ChatRequest, session_id: str, stream: bool) -> dict[str, Any]:
        context = [
            {"role": item.role, "content": item.content}
            for item in request.history
            if item.role in {"user", "assistant", "system"}
        ]
        payload: dict[str, Any] = {
            "message": request.question,
            "session_id": session_id,
            "user_id": request.user_id or "default",
            "stream": stream,
            "context": context or None,
            "need_reply": True,
        }
        if self.config.ovbot_channel_id:
            payload["channel_id"] = self.config.ovbot_channel_id
        return payload

    @staticmethod
    def _raise_for_status(resp: httpx.Response) -> None:
        if resp.status_code < 400:
            return
        raise RuntimeError(f"OV-Bot HTTP {resp.status_code}: {resp.text}")

    @staticmethod
    async def _iter_sse(resp: httpx.Response) -> AsyncIterator[tuple[str | None, str]]:
        event_name: str | None = None
        data_lines: list[str] = []

        async for line in resp.aiter_lines():
            if line == "":
                if data_lines:
                    yield event_name, "\n".join(data_lines)
                    event_name = None
                    data_lines = []
                continue
            if line.startswith(":"):
                continue
            if line.startswith("event:"):
                event_name = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data_lines.append(line[len("data:") :].strip())

        if data_lines:
            yield event_name, "\n".join(data_lines)
