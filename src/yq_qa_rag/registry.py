from __future__ import annotations

import asyncio


class RequestRegistry:
    def __init__(self) -> None:
        self._active: dict[str, asyncio.Event] = {}
        self._lock = asyncio.Lock()

    async def start(self, request_id: str) -> asyncio.Event:
        async with self._lock:
            event = asyncio.Event()
            self._active[request_id] = event
            return event

    async def finish(self, request_id: str) -> None:
        async with self._lock:
            self._active.pop(request_id, None)

    async def cancel(self, request_id: str) -> bool:
        async with self._lock:
            event = self._active.get(request_id)
            if event is None:
                return False
            event.set()
            return True


class SessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, dict] = {}

    def create(self, session_id: str, user_id: str | None, metadata: dict) -> dict:
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        record = {
            "session_id": session_id,
            "user_id": user_id,
            "created_at": now,
            "last_active": now,
            "metadata": metadata,
        }
        self._sessions[session_id] = record
        return record

    def touch(self, session_id: str) -> None:
        from datetime import datetime, timezone

        if session_id in self._sessions:
            self._sessions[session_id]["last_active"] = datetime.now(timezone.utc)

    def get(self, session_id: str) -> dict | None:
        return self._sessions.get(session_id)

    def delete(self, session_id: str) -> bool:
        return self._sessions.pop(session_id, None) is not None

    def list(self) -> list[dict]:
        return list(self._sessions.values())
