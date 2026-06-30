from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from yq_rag_manager.models import DocumentCreate, MethodCreate, MethodUpdate, RagMethod, utcnow


class Store:
    def __init__(self, db_path: str) -> None:
        self.db_path = str(Path(db_path).expanduser().resolve())
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                create table if not exists rag_methods (
                    method_id text primary key,
                    backend_type text not null,
                    display_name text,
                    enabled integer not null,
                    config_json text not null,
                    status text not null,
                    worker_url text,
                    worker_port integer,
                    pid integer,
                    created_at text not null,
                    updated_at text not null,
                    last_health_json text
                )
                """
            )
            conn.execute(
                """
                create table if not exists request_log (
                    id integer primary key autoincrement,
                    method_id text not null,
                    operation text not null,
                    request_id text,
                    ok integer not null,
                    latency_ms real,
                    error text,
                    created_at text not null
                )
                """
            )
            conn.execute(
                """
                create table if not exists ingestion_jobs (
                    job_id text primary key,
                    method_id text not null,
                    status text not null,
                    total integer not null,
                    waiting integer not null,
                    submitting integer not null,
                    running integer not null,
                    completed integer not null,
                    failed integer not null,
                    options_json text not null,
                    error text,
                    created_at text not null,
                    updated_at text not null,
                    started_at text,
                    finished_at text
                )
                """
            )
            conn.execute(
                """
                create table if not exists ingestion_job_items (
                    item_id integer primary key autoincrement,
                    job_id text not null,
                    method_id text not null,
                    document_id text not null,
                    path text,
                    url text,
                    title text,
                    metadata_json text not null,
                    options_json text not null,
                    status text not null,
                    task_id text,
                    root_uri text,
                    response_json text,
                    error text,
                    created_at text not null,
                    updated_at text not null,
                    started_at text,
                    submitted_at text,
                    finished_at text
                )
                """
            )

    def create_method(self, request: MethodCreate) -> RagMethod:
        now = utcnow().isoformat()
        with self._connect() as conn:
            try:
                conn.execute(
                    """
                    insert into rag_methods (
                        method_id, backend_type, display_name, enabled, config_json,
                        status, created_at, updated_at
                    ) values (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        request.method_id,
                        request.backend_type,
                        request.display_name,
                        1 if request.enabled else 0,
                        json.dumps(request.config, ensure_ascii=False),
                        "registered",
                        now,
                        now,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError(f"method already exists: {request.method_id}") from exc
        return self.get_method(request.method_id)

    def list_methods(self) -> list[RagMethod]:
        with self._connect() as conn:
            rows = conn.execute("select * from rag_methods order by created_at").fetchall()
        return [self._row_to_method(row) for row in rows]

    def get_method(self, method_id: str) -> RagMethod:
        with self._connect() as conn:
            row = conn.execute(
                "select * from rag_methods where method_id = ?", (method_id,)
            ).fetchone()
        if row is None:
            raise KeyError(method_id)
        return self._row_to_method(row)

    def update_method(self, method_id: str, request: MethodUpdate) -> RagMethod:
        current = self.get_method(method_id)
        display_name = request.display_name if request.display_name is not None else current.display_name
        enabled = request.enabled if request.enabled is not None else current.enabled
        config = request.config if request.config is not None else current.config
        with self._connect() as conn:
            conn.execute(
                """
                update rag_methods
                set display_name = ?, enabled = ?, config_json = ?, updated_at = ?
                where method_id = ?
                """,
                (
                    display_name,
                    1 if enabled else 0,
                    json.dumps(config, ensure_ascii=False),
                    utcnow().isoformat(),
                    method_id,
                ),
            )
        return self.get_method(method_id)

    def delete_method(self, method_id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute("delete from rag_methods where method_id = ?", (method_id,))
        return cur.rowcount > 0

    def update_runtime(
        self,
        method_id: str,
        *,
        status: str,
        worker_url: str | None = None,
        worker_port: int | None = None,
        pid: int | None = None,
    ) -> RagMethod:
        with self._connect() as conn:
            conn.execute(
                """
                update rag_methods
                set status = ?, worker_url = ?, worker_port = ?, pid = ?, updated_at = ?
                where method_id = ?
                """,
                (status, worker_url, worker_port, pid, utcnow().isoformat(), method_id),
            )
        return self.get_method(method_id)

    def update_status(self, method_id: str, status: str) -> RagMethod:
        current = self.get_method(method_id)
        return self.update_runtime(
            method_id,
            status=status,
            worker_url=current.worker_url,
            worker_port=current.worker_port,
            pid=current.pid,
        )

    def update_health(self, method_id: str, health: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                update rag_methods
                set last_health_json = ?, updated_at = ?
                where method_id = ?
                """,
                (json.dumps(health, ensure_ascii=False), utcnow().isoformat(), method_id),
            )

    def log_request(
        self,
        method_id: str,
        operation: str,
        request_id: str | None,
        ok: bool,
        latency_ms: float | None,
        error: str | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                insert into request_log (
                    method_id, operation, request_id, ok, latency_ms, error, created_at
                ) values (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    method_id,
                    operation,
                    request_id,
                    1 if ok else 0,
                    latency_ms,
                    error,
                    utcnow().isoformat(),
                ),
            )

    def create_ingestion_job(
        self,
        method_id: str,
        documents: list[DocumentCreate],
        *,
        options: dict[str, Any],
        max_concurrency: int,
        poll_interval_sec: float,
    ) -> dict[str, Any]:
        job_id = str(uuid4())
        now = utcnow().isoformat()
        merged_options = {
            "max_concurrency": max_concurrency,
            "poll_interval_sec": poll_interval_sec,
            **options,
        }
        with self._connect() as conn:
            conn.execute(
                """
                insert into ingestion_jobs (
                    job_id, method_id, status, total, waiting, submitting, running,
                    completed, failed, options_json, created_at, updated_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    method_id,
                    "queued",
                    len(documents),
                    len(documents),
                    0,
                    0,
                    0,
                    0,
                    json.dumps(merged_options, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            for document in documents:
                document_id = document.document_id or document.title or document.path or document.url
                if not document_id:
                    document_id = f"document-{uuid4()}"
                conn.execute(
                    """
                    insert into ingestion_job_items (
                        job_id, method_id, document_id, path, url, title,
                        metadata_json, options_json, status, created_at, updated_at
                    ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        job_id,
                        method_id,
                        str(document_id),
                        document.path,
                        document.url,
                        document.title,
                        json.dumps(document.metadata, ensure_ascii=False),
                        json.dumps(document.options, ensure_ascii=False),
                        "waiting",
                        now,
                        now,
                    ),
                )
        return self.get_ingestion_job(job_id)

    def get_ingestion_job(self, job_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "select * from ingestion_jobs where job_id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise KeyError(job_id)
            items = conn.execute(
                "select * from ingestion_job_items where job_id = ? order by item_id",
                (job_id,),
            ).fetchall()
        return self._job_to_dict(row, items)

    def list_ingestion_jobs(self, method_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        params: tuple[Any, ...]
        where = ""
        if method_id:
            where = "where method_id = ?"
            params = (method_id, int(limit))
        else:
            params = (int(limit),)
        with self._connect() as conn:
            rows = conn.execute(
                f"select * from ingestion_jobs {where} order by created_at desc limit ?",
                params,
            ).fetchall()
        return [self.get_ingestion_job(row["job_id"]) for row in rows]

    def next_ingestion_items(self, job_id: str, limit: int) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                select * from ingestion_job_items
                where job_id = ? and status = 'waiting'
                order by item_id
                limit ?
                """,
                (job_id, int(limit)),
            ).fetchall()
        return [self._item_to_dict(row) for row in rows]

    def count_ingestion_items(self, job_id: str, statuses: set[str]) -> int:
        if not statuses:
            return 0
        placeholders = ", ".join("?" for _ in statuses)
        with self._connect() as conn:
            row = conn.execute(
                f"""
                select count(*) as c from ingestion_job_items
                where job_id = ? and status in ({placeholders})
                """,
                (job_id, *sorted(statuses)),
            ).fetchone()
        return int(row["c"] or 0)

    def mark_ingestion_job_started(self, job_id: str) -> None:
        now = utcnow().isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                update ingestion_job_items
                set status = 'waiting', updated_at = ?
                where job_id = ? and status = 'submitting'
                """,
                (now, job_id),
            )
            conn.execute(
                """
                update ingestion_jobs
                set status = 'running', started_at = coalesce(started_at, ?), updated_at = ?
                where job_id = ? and status in ('queued', 'running')
                """,
                (now, now, job_id),
            )
        self.refresh_ingestion_job_counts(job_id)

    def update_ingestion_job_status(self, job_id: str, status: str, error: str | None = None) -> None:
        now = utcnow().isoformat()
        finished = now if status in {"completed", "failed"} else None
        with self._connect() as conn:
            conn.execute(
                """
                update ingestion_jobs
                set status = ?, error = coalesce(?, error), updated_at = ?,
                    finished_at = coalesce(?, finished_at)
                where job_id = ?
                """,
                (status, error, now, finished, job_id),
            )
        self.refresh_ingestion_job_counts(job_id)

    def update_ingestion_item(
        self,
        item_id: int,
        *,
        status: str,
        task_id: str | None = None,
        root_uri: str | None = None,
        response: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        now = utcnow().isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                update ingestion_job_items
                set status = ?,
                    task_id = coalesce(?, task_id),
                    root_uri = coalesce(?, root_uri),
                    response_json = coalesce(?, response_json),
                    error = ?,
                    updated_at = ?,
                    started_at = case when ? in ('submitting', 'running') then coalesce(started_at, ?) else started_at end,
                    submitted_at = case when ? = 'running' then coalesce(submitted_at, ?) else submitted_at end,
                    finished_at = case when ? in ('completed', 'failed') then coalesce(finished_at, ?) else finished_at end
                where item_id = ?
                """,
                (
                    status,
                    task_id,
                    root_uri,
                    json.dumps(response, ensure_ascii=False) if response is not None else None,
                    error,
                    now,
                    status,
                    now,
                    status,
                    now,
                    status,
                    now,
                    item_id,
                ),
            )

    def refresh_ingestion_job_counts(self, job_id: str) -> None:
        with self._connect() as conn:
            rows = conn.execute(
                """
                select status, count(*) as c
                from ingestion_job_items
                where job_id = ?
                group by status
                """,
                (job_id,),
            ).fetchall()
            counts = {row["status"]: int(row["c"] or 0) for row in rows}
            total = sum(counts.values())
            waiting = counts.get("waiting", 0)
            submitting = counts.get("submitting", 0)
            running = counts.get("running", 0)
            completed = counts.get("completed", 0)
            failed = counts.get("failed", 0)
            now = utcnow().isoformat()
            conn.execute(
                """
                update ingestion_jobs
                set total = ?, waiting = ?, submitting = ?, running = ?,
                    completed = ?, failed = ?, updated_at = ?
                where job_id = ?
                """,
                (total, waiting, submitting, running, completed, failed, now, job_id),
            )

    def stats(self, method_id: str | None = None) -> dict[str, Any]:
        params: tuple[Any, ...] = (method_id,) if method_id else ()
        where = "where method_id = ?" if method_id else ""
        with self._connect() as conn:
            total = conn.execute(f"select count(*) as c from request_log {where}", params).fetchone()[
                "c"
            ]
            success = conn.execute(
                f"select count(*) as c from request_log {where} {'and' if where else 'where'} ok = 1",
                params,
            ).fetchone()["c"]
            failed = conn.execute(
                f"select count(*) as c from request_log {where} {'and' if where else 'where'} ok = 0",
                params,
            ).fetchone()["c"]
            avg = conn.execute(
                f"select avg(latency_ms) as v from request_log {where}", params
            ).fetchone()["v"]
            rows = conn.execute(
                """
                select method_id, count(*) as total, sum(ok) as success, avg(latency_ms) as avg_latency
                from request_log
                group by method_id
                """
            ).fetchall()
        return {
            "total_requests": int(total or 0),
            "success_requests": int(success or 0),
            "failed_requests": int(failed or 0),
            "avg_latency_ms": float(avg) if avg is not None else None,
            "by_method": {
                row["method_id"]: {
                    "total": int(row["total"] or 0),
                    "success": int(row["success"] or 0),
                    "failed": int((row["total"] or 0) - (row["success"] or 0)),
                    "avg_latency_ms": float(row["avg_latency"])
                    if row["avg_latency"] is not None
                    else None,
                }
                for row in rows
            },
        }

    def _job_to_dict(self, row: sqlite3.Row, items: list[sqlite3.Row]) -> dict[str, Any]:
        return {
            "job_id": row["job_id"],
            "method_id": row["method_id"],
            "status": row["status"],
            "counts": {
                "total": int(row["total"] or 0),
                "waiting": int(row["waiting"] or 0),
                "submitting": int(row["submitting"] or 0),
                "running": int(row["running"] or 0),
                "completed": int(row["completed"] or 0),
                "failed": int(row["failed"] or 0),
            },
            "options": json.loads(row["options_json"] or "{}"),
            "error": row["error"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "items": [self._item_to_dict(item) for item in items],
        }

    def _item_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "item_id": int(row["item_id"]),
            "job_id": row["job_id"],
            "method_id": row["method_id"],
            "document_id": row["document_id"],
            "path": row["path"],
            "url": row["url"],
            "title": row["title"],
            "metadata": json.loads(row["metadata_json"] or "{}"),
            "options": json.loads(row["options_json"] or "{}"),
            "status": row["status"],
            "task_id": row["task_id"],
            "root_uri": row["root_uri"],
            "response": json.loads(row["response_json"]) if row["response_json"] else None,
            "error": row["error"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "started_at": row["started_at"],
            "submitted_at": row["submitted_at"],
            "finished_at": row["finished_at"],
        }

    def _row_to_method(self, row: sqlite3.Row) -> RagMethod:
        return RagMethod(
            method_id=row["method_id"],
            backend_type=row["backend_type"],
            display_name=row["display_name"],
            enabled=bool(row["enabled"]),
            config=json.loads(row["config_json"] or "{}"),
            status=row["status"],
            worker_url=row["worker_url"],
            worker_port=row["worker_port"],
            pid=row["pid"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            last_health=json.loads(row["last_health_json"])
            if row["last_health_json"]
            else None,
        )
