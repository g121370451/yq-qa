from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from yq_qa_rag.models import (
    DocumentJobDetail,
    DocumentJobProgress,
    DocumentJobStatus,
    DocumentJobSummary,
    MethodAnswer,
    QaRuntimeConfig,
    QaRuntimeConfigPublic,
    QaRuntimeConfigUpdate,
    QaTaskCreate,
    QaTaskDetail,
    QaTaskSummary,
    TaskEvent,
    TaskStatus,
    UploadedDocument,
)


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class TaskStore:
    def __init__(self, db_path: str) -> None:
        if db_path == ":memory:":
            self.db_path = db_path
        else:
            self.db_path = str(Path(db_path).expanduser().resolve())
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._migrate()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _migrate(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS qa_tasks (
                    task_id TEXT PRIMARY KEY,
                    request_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    question TEXT NOT NULL,
                    user_id TEXT,
                    session_id TEXT,
                    method_ids_json TEXT NOT NULL,
                    merge_strategy TEXT NOT NULL,
                    history_json TEXT NOT NULL,
                    options_json TEXT NOT NULL,
                    per_method_options_json TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    merged_answer TEXT,
                    error TEXT,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT
                );

                CREATE TABLE IF NOT EXISTS qa_task_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    method_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    answer TEXT NOT NULL DEFAULT '',
                    sources_json TEXT NOT NULL,
                    latency_ms REAL,
                    error TEXT,
                    backend_metadata_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(task_id, method_id),
                    FOREIGN KEY(task_id) REFERENCES qa_tasks(task_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS qa_task_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    message TEXT,
                    data_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES qa_tasks(task_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS qa_runtime_config (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS document_jobs (
                    job_id TEXT PRIMARY KEY,
                    method_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    manager_job_id TEXT,
                    documents_json TEXT NOT NULL,
                    options_json TEXT NOT NULL,
                    manager_response_json TEXT,
                    progress_json TEXT NOT NULL,
                    error TEXT,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT
                );

                CREATE TABLE IF NOT EXISTS document_job_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    message TEXT,
                    data_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(job_id) REFERENCES document_jobs(job_id) ON DELETE CASCADE
                );
                """
            )
            self._conn.commit()

    def ensure_runtime_config(self, defaults: QaRuntimeConfig) -> QaRuntimeConfig:
        with self._lock:
            row = self._conn.execute(
                "SELECT payload_json FROM qa_runtime_config WHERE id = 1"
            ).fetchone()
            if row is None:
                now = utcnow_iso()
                self._conn.execute(
                    """
                    INSERT INTO qa_runtime_config (id, payload_json, created_at, updated_at)
                    VALUES (1, ?, ?, ?)
                    """,
                    (defaults.model_dump_json(), now, now),
                )
                self._conn.commit()
                return defaults
            return QaRuntimeConfig.model_validate_json(row["payload_json"])

    def get_runtime_config(self) -> QaRuntimeConfig:
        with self._lock:
            row = self._conn.execute(
                "SELECT payload_json FROM qa_runtime_config WHERE id = 1"
            ).fetchone()
        if row is None:
            return QaRuntimeConfig()
        return QaRuntimeConfig.model_validate_json(row["payload_json"])

    def update_runtime_config(self, update: QaRuntimeConfigUpdate) -> QaRuntimeConfig:
        current = self.get_runtime_config()
        data = current.model_dump()
        changes = update.model_dump(exclude_unset=True)
        data.update(changes)
        next_config = QaRuntimeConfig.model_validate(data)
        now = utcnow_iso()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO qa_runtime_config (id, payload_json, created_at, updated_at)
                VALUES (1, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    updated_at = excluded.updated_at
                """,
                (next_config.model_dump_json(), now, now),
            )
            self._conn.commit()
        return next_config

    def public_runtime_config(self, db_path: str | None = None) -> QaRuntimeConfigPublic:
        config = self.get_runtime_config()
        return QaRuntimeConfigPublic(
            rag_manager_base_url=config.rag_manager_base_url,
            rag_manager_timeout_seconds=config.rag_manager_timeout_seconds,
            default_method_ids=config.default_method_ids,
            max_concurrent_tasks=config.max_concurrent_tasks,
            method_timeout_seconds=config.method_timeout_seconds,
            upload_dir=config.upload_dir,
            max_concurrent_ingestion_jobs=config.max_concurrent_ingestion_jobs,
            merge_enabled=config.merge_enabled,
            merge_base_url=config.merge_base_url,
            merge_api_key_set=bool(config.merge_api_key),
            merge_api_key_masked=_mask_secret(config.merge_api_key),
            merge_model=config.merge_model,
            merge_timeout_seconds=config.merge_timeout_seconds,
            merge_temperature=config.merge_temperature,
            db_path=db_path,
        )

    def create_document_job(
        self,
        job_id: str,
        method_id: str,
        documents: list[UploadedDocument],
        options: dict[str, Any],
    ) -> DocumentJobDetail:
        now = utcnow_iso()
        progress = DocumentJobProgress(
            total_documents=len(documents),
            pending_documents=len(documents),
            message="Queued",
        )
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO document_jobs (
                    job_id, method_id, status, documents_json, options_json,
                    progress_json, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    method_id,
                    DocumentJobStatus.QUEUED.value,
                    _json([document.model_dump(mode="json") for document in documents]),
                    _json(options),
                    progress.model_dump_json(),
                    now,
                    now,
                ),
            )
            self._insert_document_event_locked(
                job_id,
                "queued",
                "Upload saved; ingestion job queued",
                {"total_documents": len(documents)},
                now,
            )
            self._conn.commit()
        return self.get_document_job(job_id)

    def get_document_job(self, job_id: str) -> DocumentJobDetail:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM document_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        if row is None:
            raise KeyError(job_id)
        return _document_job_detail(row)

    def list_document_jobs(
        self,
        limit: int = 50,
        status: str | None = None,
    ) -> list[DocumentJobSummary]:
        sql = "SELECT * FROM document_jobs"
        params: list[Any] = []
        if status:
            sql += " WHERE status = ?"
            params.append(status)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [_document_job_summary(row) for row in rows]

    def set_document_job_running(self, job_id: str) -> None:
        now = utcnow_iso()
        with self._lock:
            self._conn.execute(
                """
                UPDATE document_jobs
                SET status = 'running', updated_at = ?, started_at = COALESCE(started_at, ?)
                WHERE job_id = ?
                """,
                (now, now, job_id),
            )
            self._insert_document_event_locked(job_id, "running", "Ingestion started", {}, now)
            self._conn.commit()

    def update_document_job_progress(
        self,
        job_id: str,
        progress: DocumentJobProgress,
        *,
        manager_job_id: str | None = None,
        manager_response: dict[str, Any] | None = None,
        message: str | None = None,
    ) -> None:
        now = utcnow_iso()
        with self._lock:
            self._conn.execute(
                """
                UPDATE document_jobs
                SET progress_json = ?,
                    manager_job_id = COALESCE(?, manager_job_id),
                    manager_response_json = COALESCE(?, manager_response_json),
                    updated_at = ?
                WHERE job_id = ?
                """,
                (
                    progress.model_dump_json(),
                    manager_job_id,
                    _json(manager_response) if manager_response is not None else None,
                    now,
                    job_id,
                ),
            )
            self._insert_document_event_locked(
                job_id,
                "progress",
                message or progress.message,
                progress.model_dump(mode="json"),
                now,
            )
            self._conn.commit()

    def set_document_job_finished(
        self,
        job_id: str,
        status: DocumentJobStatus,
        *,
        progress: DocumentJobProgress | None = None,
        manager_response: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        now = utcnow_iso()
        with self._lock:
            if progress is None:
                current = self.get_document_job(job_id)
                progress = current.progress
            self._conn.execute(
                """
                UPDATE document_jobs
                SET status = ?,
                    progress_json = ?,
                    manager_response_json = COALESCE(?, manager_response_json),
                    error = ?,
                    updated_at = ?,
                    completed_at = ?
                WHERE job_id = ?
                """,
                (
                    status.value,
                    progress.model_dump_json(),
                    _json(manager_response) if manager_response is not None else None,
                    error,
                    now,
                    now,
                    job_id,
                ),
            )
            self._insert_document_event_locked(
                job_id,
                status.value,
                error or progress.message or f"Job {status.value}",
                progress.model_dump(mode="json"),
                now,
            )
            self._conn.commit()

    def request_document_job_cancel(self, job_id: str) -> DocumentJobStatus:
        now = utcnow_iso()
        with self._lock:
            row = self._conn.execute(
                "SELECT status, progress_json FROM document_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if row is None:
                raise KeyError(job_id)
            status = DocumentJobStatus(row["status"])
            if status in {DocumentJobStatus.QUEUED, DocumentJobStatus.RUNNING}:
                self._conn.execute(
                    "UPDATE document_jobs SET cancel_requested = 1, updated_at = ? WHERE job_id = ?",
                    (now, job_id),
                )
                self._insert_document_event_locked(
                    job_id, "cancel_requested", "Cancel requested", {}, now
                )
                if status == DocumentJobStatus.QUEUED:
                    progress = _loads(row["progress_json"], {})
                    progress["message"] = "Cancelled before ingestion started"
                    self._conn.execute(
                        """
                        UPDATE document_jobs
                        SET status = 'cancelled', progress_json = ?, updated_at = ?, completed_at = ?
                        WHERE job_id = ?
                        """,
                        (_json(progress), now, now, job_id),
                    )
                    status = DocumentJobStatus.CANCELLED
                self._conn.commit()
            return status

    def document_job_cancel_requested(self, job_id: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT cancel_requested FROM document_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        if row is None:
            raise KeyError(job_id)
        return bool(row["cancel_requested"])

    def list_document_events(
        self,
        job_id: str,
        after_id: int = 0,
        limit: int = 200,
    ) -> list[TaskEvent]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT event_id, job_id AS task_id, event_type, message, data_json, created_at
                FROM document_job_events
                WHERE job_id = ? AND event_id > ?
                ORDER BY event_id ASC
                LIMIT ?
                """,
                (job_id, after_id, limit),
            ).fetchall()
        return [_event(row) for row in rows]

    def mark_unfinished_failed(self) -> None:
        now = utcnow_iso()
        with self._lock:
            rows = self._conn.execute(
                "SELECT task_id FROM qa_tasks WHERE status IN ('queued', 'running')"
            ).fetchall()
            self._conn.execute(
                """
                UPDATE qa_tasks
                SET status = 'failed',
                    error = 'backend restarted before task completed',
                    updated_at = ?,
                    completed_at = ?
                WHERE status IN ('queued', 'running')
                """,
                (now, now),
            )
            for row in rows:
                self._insert_event_locked(
                    row["task_id"],
                    "failed",
                    "backend restarted before task completed",
                    {},
                    now,
                )
            self._conn.commit()

    def create_task(
        self,
        task_id: str,
        request: QaTaskCreate,
        method_ids: list[str],
        merge_strategy: str,
    ) -> QaTaskDetail:
        now = utcnow_iso()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO qa_tasks (
                    task_id, request_id, status, question, user_id, session_id,
                    method_ids_json, merge_strategy, history_json, options_json,
                    per_method_options_json, metadata_json, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    request.task_request_id(),
                    TaskStatus.QUEUED.value,
                    request.question,
                    request.user_id,
                    request.session_id,
                    _json(method_ids),
                    merge_strategy,
                    _json([item.model_dump(mode="json") for item in request.history]),
                    _json(request.options),
                    _json(request.per_method_options),
                    _json(request.metadata),
                    now,
                    now,
                ),
            )
            self._insert_event_locked(
                task_id, "queued", "task queued", {"method_ids": method_ids}, now
            )
            self._conn.commit()
        return self.get_task(task_id)

    def get_task(self, task_id: str) -> QaTaskDetail:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM qa_tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if row is None:
                raise KeyError(task_id)
            results = self._results_locked(task_id)
        return _task_detail(row, results)

    def list_tasks(self, limit: int = 50, status: str | None = None) -> list[QaTaskSummary]:
        sql = "SELECT * FROM qa_tasks"
        params: list[Any] = []
        if status:
            sql += " WHERE status = ?"
            params.append(status)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [_task_summary(row) for row in rows]

    def set_running(self, task_id: str) -> None:
        now = utcnow_iso()
        with self._lock:
            self._conn.execute(
                """
                UPDATE qa_tasks
                SET status = 'running', updated_at = ?, started_at = COALESCE(started_at, ?)
                WHERE task_id = ?
                """,
                (now, now, task_id),
            )
            self._insert_event_locked(task_id, "running", "task started", {}, now)
            self._conn.commit()

    def set_finished(
        self,
        task_id: str,
        status: TaskStatus,
        *,
        merged_answer: str | None = None,
        error: str | None = None,
    ) -> None:
        now = utcnow_iso()
        with self._lock:
            self._conn.execute(
                """
                UPDATE qa_tasks
                SET status = ?, merged_answer = ?, error = ?, updated_at = ?, completed_at = ?
                WHERE task_id = ?
                """,
                (status.value, merged_answer, error, now, now, task_id),
            )
            self._insert_event_locked(
                task_id,
                status.value,
                error or f"task {status.value}",
                {"merged_answer": merged_answer} if merged_answer else {},
                now,
            )
            self._conn.commit()

    def request_cancel(self, task_id: str) -> TaskStatus:
        now = utcnow_iso()
        with self._lock:
            row = self._conn.execute(
                "SELECT status FROM qa_tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if row is None:
                raise KeyError(task_id)
            status = TaskStatus(row["status"])
            if status in {TaskStatus.QUEUED, TaskStatus.RUNNING}:
                self._conn.execute(
                    "UPDATE qa_tasks SET cancel_requested = 1, updated_at = ? WHERE task_id = ?",
                    (now, task_id),
                )
                self._insert_event_locked(task_id, "cancel_requested", "cancel requested", {}, now)
                if status == TaskStatus.QUEUED:
                    self._conn.execute(
                        """
                        UPDATE qa_tasks
                        SET status = 'cancelled', updated_at = ?, completed_at = ?
                        WHERE task_id = ?
                        """,
                        (now, now, task_id),
                    )
                    status = TaskStatus.CANCELLED
                    self._insert_event_locked(task_id, "cancelled", "queued task cancelled", {}, now)
                self._conn.commit()
            return status

    def cancel_requested(self, task_id: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT cancel_requested FROM qa_tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        if row is None:
            raise KeyError(task_id)
        return bool(row["cancel_requested"])

    def add_event(
        self,
        task_id: str,
        event_type: str,
        message: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> TaskEvent:
        now = utcnow_iso()
        with self._lock:
            event_id = self._insert_event_locked(task_id, event_type, message, data or {}, now)
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM qa_task_events WHERE event_id = ?",
                (event_id,),
            ).fetchone()
        return _event(row)

    def list_events(self, task_id: str, after_id: int = 0, limit: int = 200) -> list[TaskEvent]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM qa_task_events
                WHERE task_id = ? AND event_id > ?
                ORDER BY event_id ASC
                LIMIT ?
                """,
                (task_id, after_id, limit),
            ).fetchall()
        return [_event(row) for row in rows]

    def save_method_result(self, task_id: str, result: MethodAnswer) -> None:
        now = result.created_at.isoformat()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO qa_task_results (
                    task_id, method_id, status, answer, sources_json, latency_ms,
                    error, backend_metadata_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id, method_id) DO UPDATE SET
                    status = excluded.status,
                    answer = excluded.answer,
                    sources_json = excluded.sources_json,
                    latency_ms = excluded.latency_ms,
                    error = excluded.error,
                    backend_metadata_json = excluded.backend_metadata_json,
                    created_at = excluded.created_at
                """,
                (
                    task_id,
                    result.method_id,
                    result.status,
                    result.answer,
                    _json([source.model_dump(mode="json") for source in result.sources]),
                    result.latency_ms,
                    result.error,
                    _json(result.backend_metadata),
                    now,
                ),
            )
            self._conn.execute(
                "UPDATE qa_tasks SET updated_at = ? WHERE task_id = ?",
                (utcnow_iso(), task_id),
            )
            self._conn.commit()

    def _results_locked(self, task_id: str) -> list[MethodAnswer]:
        rows = self._conn.execute(
            """
            SELECT * FROM qa_task_results
            WHERE task_id = ?
            ORDER BY id ASC
            """,
            (task_id,),
        ).fetchall()
        return [_method_answer(row) for row in rows]

    def _insert_event_locked(
        self,
        task_id: str,
        event_type: str,
        message: str | None,
        data: dict[str, Any],
        created_at: str,
    ) -> int:
        cursor = self._conn.execute(
            """
            INSERT INTO qa_task_events (task_id, event_type, message, data_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (task_id, event_type, message, _json(data), created_at),
        )
        return int(cursor.lastrowid)

    def _insert_document_event_locked(
        self,
        job_id: str,
        event_type: str,
        message: str | None,
        data: dict[str, Any],
        created_at: str,
    ) -> int:
        cursor = self._conn.execute(
            """
            INSERT INTO document_job_events (job_id, event_type, message, data_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (job_id, event_type, message, _json(data), created_at),
        )
        return int(cursor.lastrowid)


def _task_summary(row: sqlite3.Row) -> QaTaskSummary:
    return QaTaskSummary(
        task_id=row["task_id"],
        request_id=row["request_id"],
        status=TaskStatus(row["status"]),
        question=row["question"],
        user_id=row["user_id"],
        session_id=row["session_id"],
        method_ids=_loads(row["method_ids_json"], []),
        merge_strategy=row["merge_strategy"],
        merged_answer=row["merged_answer"],
        error=row["error"],
        created_at=_dt(row["created_at"]),
        updated_at=_dt(row["updated_at"]),
        started_at=_dt_or_none(row["started_at"]),
        completed_at=_dt_or_none(row["completed_at"]),
        metadata=_loads(row["metadata_json"], {}),
    )


def _task_detail(row: sqlite3.Row, results: list[MethodAnswer]) -> QaTaskDetail:
    summary = _task_summary(row)
    return QaTaskDetail(
        **summary.model_dump(),
        history=_loads(row["history_json"], []),
        options=_loads(row["options_json"], {}),
        per_method_options=_loads(row["per_method_options_json"], {}),
        results=results,
    )


def _method_answer(row: sqlite3.Row) -> MethodAnswer:
    return MethodAnswer(
        method_id=row["method_id"],
        status=row["status"],
        answer=row["answer"],
        sources=_loads(row["sources_json"], []),
        latency_ms=row["latency_ms"],
        error=row["error"],
        backend_metadata=_loads(row["backend_metadata_json"], {}),
        created_at=_dt(row["created_at"]),
    )


def _event(row: sqlite3.Row) -> TaskEvent:
    return TaskEvent(
        event_id=row["event_id"],
        task_id=row["task_id"],
        event_type=row["event_type"],
        message=row["message"],
        data=_loads(row["data_json"], {}),
        created_at=_dt(row["created_at"]),
    )


def _document_job_summary(row: sqlite3.Row) -> DocumentJobSummary:
    return DocumentJobSummary(
        job_id=row["job_id"],
        method_id=row["method_id"],
        status=DocumentJobStatus(row["status"]),
        manager_job_id=row["manager_job_id"],
        error=row["error"],
        progress=DocumentJobProgress.model_validate_json(row["progress_json"]),
        created_at=_dt(row["created_at"]),
        updated_at=_dt(row["updated_at"]),
        started_at=_dt_or_none(row["started_at"]),
        completed_at=_dt_or_none(row["completed_at"]),
    )


def _document_job_detail(row: sqlite3.Row) -> DocumentJobDetail:
    summary = _document_job_summary(row)
    return DocumentJobDetail(
        **summary.model_dump(),
        documents=[
            UploadedDocument.model_validate(document)
            for document in _loads(row["documents_json"], [])
        ],
        options=_loads(row["options_json"], {}),
        manager_response=_loads(row["manager_response_json"], None),
    )


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    return json.loads(value)


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _dt_or_none(value: str | None) -> datetime | None:
    if not value:
        return None
    return _dt(value)


def _mask_secret(value: str | None) -> str | None:
    if not value:
        return None
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}...{value[-4:]}"
