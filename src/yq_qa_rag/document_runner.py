from __future__ import annotations

import asyncio
from uuid import uuid4

from yq_qa_rag.models import (
    DocumentJobProgress,
    DocumentJobStatus,
    UploadedDocument,
)
from yq_qa_rag.rag_manager_client import RagManagerClient
from yq_qa_rag.store import TaskStore


class DocumentIngestionRunner:
    def __init__(self, store: TaskStore) -> None:
        self.store = store
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def create_job(
        self,
        *,
        method_id: str,
        documents: list[UploadedDocument],
        options: dict,
    ) -> str:
        job_id = str(uuid4())
        self.store.create_document_job(job_id, method_id, documents, options)
        self._tasks[job_id] = asyncio.create_task(self._run_job(job_id))
        return job_id

    async def shutdown(self) -> None:
        tasks = list(self._tasks.values())
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _run_job(self, job_id: str) -> None:
        try:
            await self._wait_for_capacity(job_id)
            if self.store.document_job_cancel_requested(job_id):
                self._cancel(job_id, "Cancelled before ingestion started")
                return
            self.store.set_document_job_running(job_id)
            job = self.store.get_document_job(job_id)
            runtime_config = self.store.get_runtime_config()
            client = RagManagerClient(runtime_config)
            progress = DocumentJobProgress(
                total_documents=len(job.documents),
                running_documents=len(job.documents),
                progress_percent=5.0,
                message="Submitting ingestion job to rag-manager",
            )
            self.store.update_document_job_progress(job_id, progress)
            manager_job = await client.create_ingestion_job(
                method_id=job.method_id,
                documents=[
                    {
                        "document_id": document.document_id,
                        "path": document.path,
                        "title": document.title or document.filename,
                        "metadata": document.metadata,
                        "options": {},
                    }
                    for document in job.documents
                ],
                options=job.options,
                max_concurrency=int(job.options.get("max_concurrency") or 1),
                poll_interval_sec=float(job.options.get("poll_interval_sec") or 2.0),
            )
            manager_job_id = str(manager_job.get("job_id") or "")
            progress = _progress_from_manager_job(manager_job, "Ingestion submitted")
            self.store.update_document_job_progress(
                job_id,
                progress,
                manager_job_id=manager_job_id,
                manager_response=manager_job,
            )
            while manager_job.get("status") not in {"completed", "failed"}:
                if self.store.document_job_cancel_requested(job_id):
                    self._cancel(job_id, "Cancel requested; rag-manager job may still finish")
                    return
                await asyncio.sleep(float(job.options.get("poll_interval_sec") or 2.0))
                manager_job = await client.get_ingestion_job(
                    method_id=job.method_id,
                    job_id=manager_job_id,
                )
                progress = _progress_from_manager_job(manager_job, "Ingestion running")
                self.store.update_document_job_progress(
                    job_id,
                    progress,
                    manager_job_id=manager_job_id,
                    manager_response=manager_job,
                )
            final_progress = _progress_from_manager_job(manager_job, "Ingestion completed")
            if manager_job.get("status") == "failed":
                self.store.set_document_job_finished(
                    job_id,
                    DocumentJobStatus.FAILED,
                    progress=final_progress,
                    manager_response=manager_job,
                    error="rag-manager ingestion job failed",
                )
            else:
                self.store.set_document_job_finished(
                    job_id,
                    DocumentJobStatus.SUCCEEDED,
                    progress=final_progress,
                    manager_response=manager_job,
                )
        except asyncio.CancelledError:
            self._cancel(job_id, "Backend shutdown")
            raise
        except Exception as exc:
            job = self.store.get_document_job(job_id)
            progress = job.progress
            progress.message = str(exc)
            self.store.set_document_job_finished(
                job_id,
                DocumentJobStatus.FAILED,
                progress=progress,
                error=str(exc),
            )
        finally:
            self._tasks.pop(job_id, None)

    async def _wait_for_capacity(self, job_id: str) -> None:
        while True:
            runtime_config = self.store.get_runtime_config()
            max_jobs = max(1, runtime_config.max_concurrent_ingestion_jobs)
            running = sum(1 for task in self._tasks.values() if not task.done())
            if running <= max_jobs:
                return
            if self.store.document_job_cancel_requested(job_id):
                return
            await asyncio.sleep(0.2)

    def _cancel(self, job_id: str, message: str) -> None:
        job = self.store.get_document_job(job_id)
        progress = job.progress
        progress.message = message
        self.store.set_document_job_finished(
            job_id,
            DocumentJobStatus.CANCELLED,
            progress=progress,
            error=message,
        )


def _progress_from_manager_job(manager_job: dict, message: str) -> DocumentJobProgress:
    counts = manager_job.get("counts") or {}
    total = int(counts.get("total") or len(manager_job.get("items") or []) or 0)
    completed = int(counts.get("completed") or 0)
    failed = int(counts.get("failed") or 0)
    running = int(counts.get("running") or 0)
    pending = int(counts.get("pending") or max(0, total - completed - failed - running))
    done = completed + failed
    progress_percent = (done / total * 100.0) if total else 0.0
    if manager_job.get("status") == "completed":
        progress_percent = 100.0
    return DocumentJobProgress(
        total_documents=total,
        completed_documents=completed,
        failed_documents=failed,
        running_documents=running,
        pending_documents=pending,
        progress_percent=round(progress_percent, 2),
        message=message,
    )
