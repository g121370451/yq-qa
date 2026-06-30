from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx

from yq_qa_rag.models import Message
from yq_qa_rag.models import QaRuntimeConfig


class RagManagerClient:
    transport: httpx.AsyncBaseTransport | None = None

    def __init__(self, config: QaRuntimeConfig) -> None:
        self.base_url = config.rag_manager_base_url.rstrip("/")
        self.timeout = config.rag_manager_timeout_seconds

    async def health(self) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=10.0, transport=self.transport) as client:
            response = await client.get(f"{self.base_url}/health")
        response.raise_for_status()
        return response.json()

    async def list_methods(self) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=30.0, transport=self.transport) as client:
            response = await client.get(f"{self.base_url}/v1/rag-methods")
        response.raise_for_status()
        return response.json()

    async def method_runtime(self, *, method_id: str) -> dict[str, Any]:
        return await self._method_request(method_id=method_id, verb="GET", suffix="/runtime")

    async def method_health(self, *, method_id: str) -> dict[str, Any]:
        return await self._method_request(method_id=method_id, verb="GET", suffix="/health")

    async def start_method(self, *, method_id: str) -> dict[str, Any]:
        return await self._method_request(method_id=method_id, verb="POST", suffix="/start")

    async def stop_method(self, *, method_id: str) -> dict[str, Any]:
        return await self._method_request(method_id=method_id, verb="POST", suffix="/stop")

    async def restart_method(self, *, method_id: str) -> dict[str, Any]:
        return await self._method_request(method_id=method_id, verb="POST", suffix="/restart")

    async def list_documents(self, *, method_id: str) -> dict[str, Any]:
        encoded_method_id = quote(method_id, safe="")
        async with httpx.AsyncClient(timeout=30.0, transport=self.transport) as client:
            response = await client.get(
                f"{self.base_url}/v1/rag-methods/{encoded_method_id}/documents"
            )
        if response.status_code >= 400:
            raise RuntimeError(f"rag-manager {response.status_code}: {response.text}")
        return response.json()

    async def _method_request(
        self,
        *,
        method_id: str,
        verb: str,
        suffix: str,
    ) -> dict[str, Any]:
        encoded_method_id = quote(method_id, safe="")
        async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
            response = await client.request(
                verb,
                f"{self.base_url}/v1/rag-methods/{encoded_method_id}{suffix}",
            )
        if response.status_code >= 400:
            raise RuntimeError(f"rag-manager {response.status_code}: {response.text}")
        return response.json()

    async def chat(
        self,
        *,
        method_id: str,
        request_id: str,
        session_id: str,
        user_id: str | None,
        question: str,
        history: list[Message],
        options: dict[str, Any],
        timeout: float | None = None,
    ) -> dict[str, Any]:
        import time

        payload = {
            "request_id": request_id,
            "session_id": session_id,
            "user_id": user_id or "default",
            "question": question,
            "history": [item.model_dump(mode="json") for item in history],
            "options": options,
        }
        started = time.perf_counter()
        async with httpx.AsyncClient(
            timeout=timeout or self.timeout,
            transport=self.transport,
        ) as client:
            response = await client.post(
                f"{self.base_url}/v1/rag-methods/{method_id}/chat",
                json=payload,
            )
        latency_ms = (time.perf_counter() - started) * 1000
        if response.status_code >= 400:
            raise RuntimeError(f"rag-manager {response.status_code}: {response.text}")
        data = response.json()
        data.setdefault("latency_ms", latency_ms)
        return data

    async def create_ingestion_job(
        self,
        *,
        method_id: str,
        documents: list[dict[str, Any]],
        options: dict[str, Any],
        max_concurrency: int,
        poll_interval_sec: float,
    ) -> dict[str, Any]:
        payload = {
            "documents": documents,
            "options": options,
            "max_concurrency": max_concurrency,
            "poll_interval_sec": poll_interval_sec,
        }
        async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
            response = await client.post(
                f"{self.base_url}/v1/rag-methods/{method_id}/ingestion-jobs",
                json=payload,
            )
        if response.status_code >= 400:
            raise RuntimeError(f"rag-manager {response.status_code}: {response.text}")
        return response.json()

    async def get_ingestion_job(self, *, method_id: str, job_id: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=30.0, transport=self.transport) as client:
            response = await client.get(
                f"{self.base_url}/v1/rag-methods/{method_id}/ingestion-jobs/{job_id}"
            )
        if response.status_code >= 400:
            raise RuntimeError(f"rag-manager {response.status_code}: {response.text}")
        return response.json()
