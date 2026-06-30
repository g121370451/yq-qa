from __future__ import annotations

import asyncio
from typing import Any

import httpx

from yq_rag_manager.models import DocumentCreate
from yq_rag_manager.store import Store


class IngestionRunner:
    def __init__(self, store: Store) -> None:
        self.store = store
        self._tasks: dict[str, asyncio.Task] = {}

    def start_job(self, job_id: str) -> None:
        task = self._tasks.get(job_id)
        if task and not task.done():
            return
        self._tasks[job_id] = asyncio.create_task(self._run_job(job_id))

    async def shutdown(self) -> None:
        for task in self._tasks.values():
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._tasks.clear()

    async def _run_job(self, job_id: str) -> None:
        try:
            self.store.mark_ingestion_job_started(job_id)
            while True:
                job = self.store.get_ingestion_job(job_id)
                options = dict(job.get("options") or {})
                max_concurrency = max(1, int(options.get("max_concurrency") or 1))
                poll_interval = max(0.5, float(options.get("poll_interval_sec") or 2.0))

                await self._refresh_running_items(job)
                self.store.refresh_ingestion_job_counts(job_id)
                job = self.store.get_ingestion_job(job_id)
                counts = job["counts"]
                if counts["waiting"] == 0 and counts["submitting"] == 0 and counts["running"] == 0:
                    status = "failed" if counts["failed"] else "completed"
                    self.store.update_ingestion_job_status(job_id, status)
                    return

                active = counts["submitting"] + counts["running"]
                slots = max(0, max_concurrency - active)
                for item in self.store.next_ingestion_items(job_id, slots):
                    await self._submit_item(item)

                self.store.refresh_ingestion_job_counts(job_id)
                await asyncio.sleep(poll_interval)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.store.update_ingestion_job_status(job_id, "failed", error=str(exc))

    async def _submit_item(self, item: dict[str, Any]) -> None:
        self.store.update_ingestion_item(item["item_id"], status="submitting")
        try:
            method = self.store.get_method(item["method_id"])
            if method.status != "running" or not method.worker_url:
                raise RuntimeError("method worker is not running")
            request = DocumentCreate(
                document_id=item["document_id"],
                path=item.get("path"),
                url=item.get("url"),
                title=item.get("title"),
                metadata=item.get("metadata") or {},
                options={**(item.get("options") or {}), "wait": False},
            )
            async with httpx.AsyncClient(timeout=None) as client:
                response = await client.post(
                    f"{method.worker_url}/documents",
                    json=request.model_dump(mode="json"),
                )
            if response.status_code >= 400:
                raise RuntimeError(response.text)
            payload = response.json()
            metadata = payload.get("metadata") or {}
            task_id = metadata.get("task_id")
            root_uri = metadata.get("root_uri")
            if not task_id:
                raise RuntimeError("OpenViking did not return ingestion task_id")
            status = "running" if task_id else payload.get("status") or "running"
            if status == "submitted":
                status = "running"
            self.store.update_ingestion_item(
                item["item_id"],
                status=status,
                task_id=task_id,
                root_uri=root_uri,
                response=payload,
            )
        except Exception as exc:
            self.store.update_ingestion_item(
                item["item_id"],
                status="failed",
                error=str(exc),
            )
        finally:
            self.store.refresh_ingestion_job_counts(item["job_id"])

    async def _refresh_running_items(self, job: dict[str, Any]) -> None:
        method = self.store.get_method(job["method_id"])
        if method.status != "running" or not method.worker_url:
            return
        running_items = [
            item for item in job.get("items") or []
            if item.get("status") == "running" and item.get("task_id")
        ]
        if not running_items:
            return
        async with httpx.AsyncClient(timeout=30.0) as client:
            for item in running_items:
                try:
                    response = await client.get(
                        f"{method.worker_url}/ingestions/{item['task_id']}"
                    )
                    if response.status_code >= 400:
                        continue
                    payload = response.json()
                    task = payload.get("task") or payload.get("result") or payload
                    task_status = str(task.get("status") or "").lower()
                    if task_status == "completed":
                        result = task.get("result") or {}
                        self.store.update_ingestion_item(
                            item["item_id"],
                            status="completed",
                            root_uri=result.get("root_uri") or item.get("root_uri"),
                            response=payload,
                        )
                    elif task_status == "failed":
                        self.store.update_ingestion_item(
                            item["item_id"],
                            status="failed",
                            response=payload,
                            error=str(task.get("error") or "OpenViking ingestion failed"),
                        )
                    elif task_status in {"pending", "running"}:
                        self.store.update_ingestion_item(
                            item["item_id"],
                            status="running",
                            response=payload,
                        )
                except Exception:
                    continue
