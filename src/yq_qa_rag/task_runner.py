from __future__ import annotations

import asyncio
from uuid import uuid4

from yq_qa_rag.config import AppConfig
from yq_qa_rag.merge import MergeClient
from yq_qa_rag.models import MethodAnswer, QaRuntimeConfig, QaTaskCreate, TaskStatus
from yq_qa_rag.rag_manager_client import RagManagerClient
from yq_qa_rag.store import TaskStore


class QaTaskRunner:
    def __init__(self, config: AppConfig, store: TaskStore) -> None:
        self.config = config
        self.store = store
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def create_task(self, request: QaTaskCreate) -> str:
        runtime_config = self.store.get_runtime_config()
        method_ids = self._method_ids(request, runtime_config)
        merge_strategy = self._merge_strategy(request, method_ids, runtime_config)
        task_id = str(uuid4())
        self.store.create_task(task_id, request, method_ids, merge_strategy)
        self._tasks[task_id] = asyncio.create_task(self._run_task(task_id))
        return task_id

    async def shutdown(self) -> None:
        tasks = list(self._tasks.values())
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def _method_ids(self, request: QaTaskCreate, runtime_config: QaRuntimeConfig) -> list[str]:
        method_ids = request.method_ids or runtime_config.default_method_ids
        method_ids = [item for item in method_ids if item]
        if not method_ids:
            raise ValueError("method_ids is required when no default_method_ids are configured")
        return method_ids

    def _merge_strategy(
        self,
        request: QaTaskCreate,
        method_ids: list[str],
        runtime_config: QaRuntimeConfig,
    ) -> str:
        merge = MergeClient(runtime_config)
        if request.merge_strategy.value == "auto":
            if len(method_ids) > 1 and merge.available():
                return "llm"
            return "none"
        if request.merge_strategy.value == "llm" and not merge.available():
            raise ValueError("merge_strategy=llm requires configured merge model")
        return request.merge_strategy.value

    async def _run_task(self, task_id: str) -> None:
        try:
            await self._wait_for_capacity(task_id)
            try:
                if self.store.cancel_requested(task_id):
                    self.store.set_finished(task_id, TaskStatus.CANCELLED, error="task cancelled")
                    return
                self.store.set_running(task_id)
                task = self.store.get_task(task_id)
                results = await asyncio.gather(
                    *[self._run_method(task_id, task, method_id) for method_id in task.method_ids],
                    return_exceptions=False,
                )
                if self.store.cancel_requested(task_id):
                    self.store.set_finished(task_id, TaskStatus.CANCELLED, error="task cancelled")
                    return
                succeeded = [item for item in results if item.status == "succeeded"]
                if not succeeded:
                    error = "; ".join(item.error or "unknown error" for item in results)
                    self.store.set_finished(task_id, TaskStatus.FAILED, error=error)
                    return

                if task.merge_strategy.value == "llm":
                    self.store.add_event(task_id, "merge_started", "merge answer started")
                    merged_answer = await MergeClient(self.store.get_runtime_config()).merge(
                        task.question,
                        succeeded,
                    )
                    self.store.add_event(task_id, "merge_finished", "merge answer finished")
                elif len(succeeded) == 1:
                    merged_answer = succeeded[0].answer
                else:
                    merged_answer = _join_answers(succeeded)

                self.store.set_finished(
                    task_id,
                    TaskStatus.SUCCEEDED,
                    merged_answer=merged_answer,
                )
            except asyncio.CancelledError:
                self.store.set_finished(task_id, TaskStatus.CANCELLED, error="backend shutdown")
                raise
            except Exception as exc:
                self.store.set_finished(task_id, TaskStatus.FAILED, error=str(exc))
        finally:
            self._tasks.pop(task_id, None)

    async def _wait_for_capacity(self, task_id: str) -> None:
        while True:
            runtime_config = self.store.get_runtime_config()
            max_tasks = max(1, runtime_config.max_concurrent_tasks)
            running = sum(1 for task in self._tasks.values() if not task.done())
            if running <= max_tasks:
                return
            if self.store.cancel_requested(task_id):
                return
            await asyncio.sleep(0.2)

    async def _run_method(self, task_id: str, task, method_id: str) -> MethodAnswer:
        self.store.add_event(
            task_id,
            "method_started",
            f"method started: {method_id}",
            {"method_id": method_id},
        )
        options = dict(task.options)
        options.update(task.per_method_options.get(method_id, {}))
        try:
            runtime_config = self.store.get_runtime_config()
            response = await RagManagerClient(runtime_config).chat(
                method_id=method_id,
                request_id=f"{task_id}:{method_id}",
                session_id=f"{task.session_id or task_id}:{method_id}",
                user_id=task.user_id,
                question=task.question,
                history=task.history,
                options=options,
                timeout=runtime_config.method_timeout_seconds,
            )
            result = MethodAnswer(
                method_id=method_id,
                status="succeeded",
                answer=str(response.get("answer") or ""),
                sources=response.get("sources") or [],
                latency_ms=response.get("latency_ms"),
                backend_metadata=response.get("backend_metadata") or {},
            )
        except Exception as exc:
            result = MethodAnswer(
                method_id=method_id,
                status="failed",
                error=str(exc),
            )
        self.store.save_method_result(task_id, result)
        self.store.add_event(
            task_id,
            "method_finished",
            f"method {result.status}: {method_id}",
            {
                "method_id": method_id,
                "status": result.status,
                "latency_ms": result.latency_ms,
                "error": result.error,
            },
        )
        return result


def _join_answers(results: list[MethodAnswer]) -> str:
    blocks = []
    for result in results:
        blocks.append(f"## {result.method_id}\n\n{result.answer}".strip())
    return "\n\n".join(blocks)
