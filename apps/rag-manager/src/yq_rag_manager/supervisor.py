from __future__ import annotations

import contextlib
import json
import os
import asyncio
import socket
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx

from yq_rag_manager.diagnostics import print_json_block
from yq_rag_manager.models import RagMethod, RuntimeResponse
from yq_rag_manager.store import Store


class WorkerSupervisor:
    def __init__(
        self,
        store: Store,
        host: str = "127.0.0.1",
        base_port: int = 18100,
        logs_dir: str | Path = "logs",
    ) -> None:
        self.store = store
        self.host = host
        self.base_port = base_port
        self.logs_dir = Path(logs_dir).expanduser().resolve()
        self._processes: dict[str, subprocess.Popen] = {}

    async def startup(self) -> None:
        recovered: list[dict[str, Any]] = []
        for method in self.store.list_methods():
            if method.status not in {"running", "starting"}:
                continue
            method = await self._recover_runtime(method)
            recovered.append(
                {
                    "method_id": method.method_id,
                    "status": method.status,
                    "worker_url": method.worker_url,
                    "worker_port": method.worker_port,
                    "pid": method.pid,
                }
            )
        if recovered:
            print_json_block("runtime recovery", recovered)

    async def shutdown(self) -> None:
        stopped: list[dict[str, Any]] = []
        for method in self.store.list_methods():
            if method.status not in {"running", "starting"}:
                continue
            runtime = await self.stop(method.method_id)
            stopped.append(runtime.model_dump(mode="json"))
        if stopped:
            print_json_block("shutdown stopped methods", stopped)

    def runtime(self, method_id: str) -> RuntimeResponse:
        method = self._refresh_process_status(self.store.get_method(method_id))
        return RuntimeResponse(
            method_id=method.method_id,
            status=method.status,
            worker_url=method.worker_url,
            worker_port=method.worker_port,
            pid=method.pid,
        )

    async def start(self, method_id: str) -> RuntimeResponse:
        method = self.store.get_method(method_id)
        if not method.enabled:
            raise ValueError(f"method is disabled: {method_id}")

        method = self._refresh_process_status(method)
        if method.status == "running" and method.worker_url:
            return self.runtime(method_id)

        port = int(method.config.get("worker_port") or method.worker_port or self._free_port())
        worker_url = f"http://{self.host}:{port}"
        self.store.update_runtime(
            method_id, status="starting", worker_url=worker_url, worker_port=port, pid=None
        )

        command = self._worker_command(method, port)
        cwd = self._worker_cwd(method)
        stdout_path = self.logs_dir / f"{method.method_id}.out.log"
        stderr_path = self.logs_dir / f"{method.method_id}.err.log"
        print_json_block(
            "starting method",
            {
                "method_id": method.method_id,
                "backend_type": method.backend_type,
                "display_name": method.display_name,
                "enabled": method.enabled,
                "status": method.status,
                "worker": {
                    "host": self.host,
                    "port": port,
                    "url": worker_url,
                    "cwd": str(cwd),
                    "command": command,
                    "stdout_log": str(stdout_path),
                    "stderr_log": str(stderr_path),
                },
                "config": method.config,
            },
        )
        env = os.environ.copy()
        env["YQ_RAG_METHOD_ID"] = method.method_id
        env["YQ_RAG_METHOD_CONFIG"] = json.dumps(method.config, ensure_ascii=False)
        env["YQ_RAG_WORKER_HOST"] = self.host
        env["YQ_RAG_WORKER_PORT"] = str(port)
        worker_env = method.config.get("env")
        if isinstance(worker_env, dict):
            for key, value in worker_env.items():
                if isinstance(key, str) and key:
                    env[key] = str(value)

        self.logs_dir.mkdir(parents=True, exist_ok=True)
        stdout = open(stdout_path, "a", encoding="utf-8")
        stderr = open(stderr_path, "a", encoding="utf-8")
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        process = subprocess.Popen(
            command,
            cwd=str(cwd),
            env=env,
            stdout=stdout,
            stderr=stderr,
            creationflags=creationflags,
        )
        self._processes[method_id] = process
        self.store.update_runtime(
            method_id,
            status="running",
            worker_url=worker_url,
            worker_port=port,
            pid=process.pid,
        )

        try:
            await self._wait_for_health(worker_url)
        except Exception:
            if process.poll() is None:
                self._terminate_process_tree(process)
            self._processes.pop(method_id, None)
            self.store.update_runtime(
                method_id,
                status="crashed",
                worker_url=worker_url,
                worker_port=port,
                pid=None,
            )
            raise
        return self.runtime(method_id)

    async def stop(self, method_id: str) -> RuntimeResponse:
        method = self.store.get_method(method_id)
        process = self._processes.get(method_id)
        if process and process.poll() is None:
            self._terminate_process_tree(process)
        elif method.pid and self._is_expected_worker_pid(method, method.pid):
            self._terminate_pid_tree(method.pid)
        self._processes.pop(method_id, None)
        self.store.update_runtime(
            method_id,
            status="stopped",
            worker_url=method.worker_url,
            worker_port=method.worker_port,
            pid=None,
        )
        return self.runtime(method_id)

    async def restart(self, method_id: str) -> RuntimeResponse:
        await self.stop(method_id)
        return await self.start(method_id)

    def _refresh_process_status(self, method: RagMethod) -> RagMethod:
        process = self._processes.get(method.method_id)
        if process is not None and process.poll() is not None and method.status == "running":
            return self.store.update_runtime(
                method.method_id,
                status="crashed",
                worker_url=method.worker_url,
                worker_port=method.worker_port,
                pid=None,
            )
        if process is None and method.status == "running":
            if method.pid and self._is_expected_worker_pid(method, method.pid):
                return method
            if method.worker_url and self._is_worker_url_ready(method.worker_url):
                return method
            return self.store.update_runtime(
                method.method_id,
                status="crashed",
                worker_url=method.worker_url,
                worker_port=method.worker_port,
                pid=None,
            )
        return method

    async def _recover_runtime(self, method: RagMethod) -> RagMethod:
        if method.pid and self._is_expected_worker_pid(method, method.pid):
            if method.worker_url and await self._is_worker_url_ready_async(method.worker_url):
                return self.store.update_runtime(
                    method.method_id,
                    status="running",
                    worker_url=method.worker_url,
                    worker_port=method.worker_port,
                    pid=method.pid,
                )
            return self.store.update_runtime(
                method.method_id,
                status="running",
                worker_url=method.worker_url,
                worker_port=method.worker_port,
                pid=method.pid,
            )
        if method.worker_url and await self._is_worker_url_ready_async(method.worker_url):
            return self.store.update_runtime(
                method.method_id,
                status="running",
                worker_url=method.worker_url,
                worker_port=method.worker_port,
                pid=method.pid,
            )
        return self.store.update_runtime(
            method.method_id,
            status="crashed" if method.status == "running" else "stopped",
            worker_url=method.worker_url,
            worker_port=method.worker_port,
            pid=None,
        )

    def _worker_command(self, method: RagMethod, port: int) -> list[str]:
        project_path_raw = str(method.config.get("project_path") or "").strip()
        if not project_path_raw:
            raise ValueError(f"{method.method_id} config.project_path is required")
        project_path = Path(project_path_raw).expanduser()
        if method.backend_type == "openviking_bot":
            script = "openviking-bot-worker"
        elif method.backend_type == "deepread":
            script = "deepread-worker"
        else:
            raise ValueError(f"unsupported backend type: {method.backend_type}")

        return [
            "uv",
            "run",
            script,
            "--host",
            self.host,
            "--port",
            str(port),
        ]

    def _worker_cwd(self, method: RagMethod) -> Path:
        project_path = Path(str(method.config.get("project_path", ""))).expanduser().resolve()
        if not project_path.exists():
            raise FileNotFoundError(f"worker project_path not found: {project_path}")
        return project_path

    async def _wait_for_health(self, worker_url: str) -> None:
        deadline = time.time() + 30
        last_error: Exception | None = None
        while time.time() < deadline:
            try:
                async with httpx.AsyncClient(timeout=2.0) as client:
                    res = await client.get(f"{worker_url}/health")
                    if res.status_code < 500:
                        return
            except Exception as exc:
                last_error = exc
            await asyncio.sleep(0.4)
        if last_error:
            raise RuntimeError(f"worker did not become healthy: {last_error}") from last_error
        raise RuntimeError("worker did not become healthy")

    async def _is_worker_url_ready_async(self, worker_url: str) -> bool:
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                res = await client.get(f"{worker_url}/health")
            return res.status_code < 500
        except Exception:
            return False

    def _is_worker_url_ready(self, worker_url: str) -> bool:
        with contextlib.suppress(Exception):
            with httpx.Client(timeout=2.0) as client:
                res = client.get(f"{worker_url}/health")
            return res.status_code < 500
        return False

    def _free_port(self) -> int:
        used = {m.worker_port for m in self.store.list_methods() if m.worker_port}
        for port in range(self.base_port, self.base_port + 1000):
            if port in used:
                continue
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(0.2)
                if sock.connect_ex((self.host, port)) != 0:
                    return port
        raise RuntimeError("no free worker port found")

    def _terminate_process_tree(self, process: subprocess.Popen) -> None:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            return

        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    def _terminate_pid_tree(self, pid: int) -> None:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            return

        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, 15)
        deadline = time.time() + 5
        while time.time() < deadline:
            if not self._pid_exists(pid):
                return
            time.sleep(0.2)
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, 9)

    def _pid_exists(self, pid: int) -> bool:
        if pid <= 0:
            return False
        if os.name == "nt":
            result = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    f"if (Get-Process -Id {int(pid)} -ErrorAction SilentlyContinue) {{ exit 0 }} else {{ exit 1 }}",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            return result.returncode == 0
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True

    def _is_expected_worker_pid(self, method: RagMethod, pid: int) -> bool:
        if not self._pid_exists(pid):
            return False
        command_line = self._process_command_line(pid).lower()
        if not command_line:
            return False
        if method.backend_type == "openviking_bot":
            expected_script = "openviking-bot-worker"
        elif method.backend_type == "deepread":
            expected_script = "deepread-worker"
        else:
            expected_script = ""
        port_text = str(method.worker_port or "")
        has_script = bool(expected_script and expected_script in command_line)
        has_port = bool(port_text and port_text in command_line)
        return has_script and has_port

    def _process_command_line(self, pid: int) -> str:
        if os.name == "nt":
            command = (
                "Get-CimInstance Win32_Process "
                f"-Filter \"ProcessId = {int(pid)}\" | "
                "Select-Object -ExpandProperty CommandLine"
            )
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", command],
                capture_output=True,
                text=True,
                check=False,
            )
            return result.stdout.strip()
        return ""
