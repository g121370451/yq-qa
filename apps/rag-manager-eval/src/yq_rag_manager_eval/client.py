from __future__ import annotations

import time
from typing import Any

import httpx

from yq_rag_manager_eval.models import EvalItem


class RagManagerClient:
    def __init__(self, base_url: str, timeout: float = 600.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def ensure_started(self, method_id: str) -> dict[str, Any]:
        with httpx.Client(timeout=self.timeout) as client:
            runtime = client.get(f"{self.base_url}/v1/rag-methods/{method_id}/runtime")
            runtime.raise_for_status()
            data = runtime.json()
            if data.get("status") == "running":
                return data
            response = client.post(f"{self.base_url}/v1/rag-methods/{method_id}/start")
            response.raise_for_status()
            return response.json()

    def ensure_method(self, method_id: str, manager_config: dict[str, Any]) -> dict[str, Any]:
        if not manager_config.get("auto_create", False):
            return self.get_method(method_id)

        payload = {
            "method_id": method_id,
            "backend_type": manager_config["backend_type"],
            "display_name": manager_config.get("display_name"),
            "enabled": manager_config.get("enabled", True),
            "config": manager_config.get("method_config", {}),
        }
        with httpx.Client(timeout=self.timeout) as client:
            existing = client.get(f"{self.base_url}/v1/rag-methods/{method_id}")
            if existing.status_code == 404:
                response = client.post(f"{self.base_url}/v1/rag-methods", json=payload)
                response.raise_for_status()
                return response.json()
            existing.raise_for_status()
            if manager_config.get("update_existing", False):
                response = client.patch(
                    f"{self.base_url}/v1/rag-methods/{method_id}",
                    json={
                        "display_name": payload["display_name"],
                        "enabled": payload["enabled"],
                        "config": payload["config"],
                    },
                )
                response.raise_for_status()
                return response.json()
            return existing.json()

    def get_method(self, method_id: str) -> dict[str, Any]:
        with httpx.Client(timeout=self.timeout) as client:
            response = client.get(f"{self.base_url}/v1/rag-methods/{method_id}")
            response.raise_for_status()
            return response.json()

    def health(self, method_id: str) -> dict[str, Any]:
        with httpx.Client(timeout=self.timeout) as client:
            response = client.get(f"{self.base_url}/v1/rag-methods/{method_id}/health")
            response.raise_for_status()
            return response.json()

    def create_document(
        self,
        method_id: str,
        document_id: str,
        path: str,
        title: str | None,
        metadata: dict[str, Any],
        options: dict[str, Any],
    ) -> dict[str, Any]:
        payload = {
            "document_id": document_id,
            "path": path,
            "title": title,
            "metadata": metadata,
            "options": options,
        }
        started = time.perf_counter()
        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(
                f"{self.base_url}/v1/rag-methods/{method_id}/documents",
                json=payload,
            )
        latency_ms = (time.perf_counter() - started) * 1000
        response.raise_for_status()
        data = response.json()
        data.setdefault("latency_ms", latency_ms)
        return data

    def create_ingestion_job(
        self,
        method_id: str,
        documents: list[dict[str, Any]],
        *,
        options: dict[str, Any],
        max_concurrency: int = 1,
        poll_interval_sec: float = 2.0,
    ) -> dict[str, Any]:
        payload = {
            "documents": documents,
            "options": {},
            "max_concurrency": max_concurrency,
            "poll_interval_sec": poll_interval_sec,
        }
        if options:
            for document in payload["documents"]:
                document["options"] = {**(document.get("options") or {}), **options}
        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(
                f"{self.base_url}/v1/rag-methods/{method_id}/ingestion-jobs",
                json=payload,
            )
        response.raise_for_status()
        return response.json()

    def get_ingestion_job(self, method_id: str, job_id: str) -> dict[str, Any]:
        with httpx.Client(timeout=self.timeout) as client:
            response = client.get(
                f"{self.base_url}/v1/rag-methods/{method_id}/ingestion-jobs/{job_id}"
            )
        response.raise_for_status()
        return response.json()

    def delete_document(self, method_id: str, document_id: str) -> dict[str, Any]:
        started = time.perf_counter()
        with httpx.Client(timeout=self.timeout) as client:
            response = client.delete(
                f"{self.base_url}/v1/rag-methods/{method_id}/documents/{document_id}"
            )
        latency_ms = (time.perf_counter() - started) * 1000
        response.raise_for_status()
        data = response.json()
        data.setdefault("latency_ms", latency_ms)
        return data

    def get_ingestion(self, method_id: str, task_id: str) -> dict[str, Any]:
        with httpx.Client(timeout=self.timeout) as client:
            response = client.get(
                f"{self.base_url}/v1/rag-methods/{method_id}/ingestions/{task_id}"
            )
        response.raise_for_status()
        return response.json()

    def list_ingestions(
        self,
        method_id: str,
        task_type: str | None = "add_resource",
        status: str | None = None,
        resource_id: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        params = {
            "task_type": task_type,
            "status": status,
            "resource_id": resource_id,
            "limit": limit,
        }
        with httpx.Client(timeout=self.timeout) as client:
            response = client.get(
                f"{self.base_url}/v1/rag-methods/{method_id}/ingestions",
                params={key: value for key, value in params.items() if value is not None},
            )
        response.raise_for_status()
        return response.json()

    def wait_ingestions(self, method_id: str, timeout: float | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if timeout is not None:
            payload["timeout"] = timeout
        started = time.perf_counter()
        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(
                f"{self.base_url}/v1/rag-methods/{method_id}/ingestions/wait",
                json=payload,
            )
        latency_ms = (time.perf_counter() - started) * 1000
        response.raise_for_status()
        data = response.json()
        data.setdefault("latency_ms", latency_ms)
        return data

    def retrieve(
        self,
        method_id: str,
        item: EvalItem,
        top_k: int,
        options: dict[str, Any],
    ) -> dict[str, Any]:
        payload = {
            "request_id": f"eval-{item.id}",
            "query": item.question,
            "top_k": top_k,
            "options": options,
        }
        started = time.perf_counter()
        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(
                f"{self.base_url}/v1/rag-methods/{method_id}/retrieve",
                json=payload,
            )
        latency_ms = (time.perf_counter() - started) * 1000
        response.raise_for_status()
        data = response.json()
        data.setdefault("latency_ms", latency_ms)
        return data

    def chat(self, method_id: str, item: EvalItem, options: dict[str, Any]) -> dict[str, Any]:
        payload = {
            "request_id": f"eval-{item.id}",
            "session_id": f"eval-{item.sample_id}-{item.id}",
            "user_id": "rag-manager-eval",
            "question": item.question,
            "options": options,
        }
        started = time.perf_counter()
        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(
                f"{self.base_url}/v1/rag-methods/{method_id}/chat",
                json=payload,
            )
        latency_ms = (time.perf_counter() - started) * 1000
        response.raise_for_status()
        data = response.json()
        data.setdefault("latency_ms", latency_ms)
        return data
