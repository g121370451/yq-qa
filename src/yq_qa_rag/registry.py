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
