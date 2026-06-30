from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, AsyncIterator

import httpx
from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse

from yq_rag_openviking_bot import __version__
from yq_rag_openviking_bot.config import configure_paths
from yq_rag_openviking_bot.models import (
    ChatRequest,
    ChatResponse,
    DocumentCreate,
    DocumentResponse,
    RetrieveRequest,
    RetrieveResponse,
    Source,
)
from yq_rag_openviking_bot.process_cleanup import cleanup_ports


class OpenVikingBotWorker:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.method_id = str(config.get("method_id") or "openviking-bot")
        configure_paths(config)
        self._server_process: subprocess.Popen | None = None
        self._gateway_process: subprocess.Popen | None = None

    async def startup(self) -> None:
        if self.config.get("server_mode", "external") == "managed":
            await self._start_server()
        if self._uses_server_bot_proxy():
            return
        if self.config.get("gateway_mode", "external") != "managed":
            return
        await self._start_gateway()

    async def _start_gateway(self) -> None:
        port = int(self.config.get("gateway_port") or 18790)
        if self.config.get("cleanup_on_start", True):
            cleanup_ports([port])
        ov_conf = self.config.get("ov_conf")
        command = ["vikingbot", "gateway", "--port", str(port)]
        if ov_conf:
            command.extend(["--config", str(ov_conf)])
        logs_dir = Path(str(self.config.get("logs_dir", "logs"))).expanduser()
        logs_dir.mkdir(parents=True, exist_ok=True)
        stdout = open(logs_dir / f"{self.method_id}.vikingbot-gateway.out.log", "a", encoding="utf-8")
        stderr = open(logs_dir / f"{self.method_id}.vikingbot-gateway.err.log", "a", encoding="utf-8")
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        if ov_conf:
            env["OPENVIKING_CONFIG_FILE"] = str(ov_conf)
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        self._gateway_process = subprocess.Popen(
            command,
            cwd=self._openviking_cwd(),
            env=env,
            stdout=stdout,
            stderr=stderr,
            creationflags=creationflags,
        )
        await self._wait_bot_api()

    async def shutdown(self) -> None:
        if self._gateway_process and self._gateway_process.poll() is None:
            self._gateway_process.terminate()
            try:
                self._gateway_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._gateway_process.kill()
        if self._server_process and self._server_process.poll() is None:
            self._server_process.terminate()
            try:
                self._server_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._server_process.kill()

    async def health(self) -> dict[str, Any]:
        if self.config.get("gateway_mode", "external") == "disabled":
            server_health = await self._check_http(f"{self.server_base}/health")
            return {
                "status": "ok" if server_health["ok"] else "degraded",
                "method_id": self.method_id,
                "backend": "openviking_bot",
                "server_url": self.server_base,
                "bot_base_url": None,
                "version": __version__,
                "details": {"bot": "disabled", "server": server_health},
            }
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self.bot_base}/bot/v1/health")
            return {
                "status": "ok" if response.status_code < 500 else "degraded",
                "method_id": self.method_id,
                "backend": "openviking_bot",
                "server_url": self.server_base,
                "bot_base_url": self.bot_base,
                "bot_route": self._bot_route_mode(),
                "bot_status_code": response.status_code,
                "bot_response": _safe_json(response),
                "version": __version__,
            }
        except Exception as exc:
            return {
                "status": "degraded",
                "method_id": self.method_id,
                "backend": "openviking_bot",
                "server_url": self.server_base,
                "bot_base_url": self.bot_base,
                "bot_route": self._bot_route_mode(),
                "error": str(exc),
                "version": __version__,
            }

    @property
    def server_base(self) -> str:
        if self.config.get("server_url"):
            return str(self.config["server_url"]).rstrip("/")
        if self.config.get("openviking_server_url"):
            return str(self.config["openviking_server_url"]).rstrip("/")
        host = str(self.config.get("server_host") or "127.0.0.1")
        port = int(self.config.get("server_port") or 1933)
        return f"http://{host}:{port}"

    @property
    def gateway_base(self) -> str:
        if self.config.get("gateway_url"):
            return str(self.config["gateway_url"]).rstrip("/")
        port = int(self.config.get("gateway_port") or 18790)
        return f"http://127.0.0.1:{port}"

    @property
    def bot_base(self) -> str:
        if self.config.get("bot_base_url"):
            return str(self.config["bot_base_url"]).rstrip("/")
        if self._uses_server_bot_proxy():
            return self.server_base
        return self.gateway_base

    def headers(self) -> dict[str, str]:
        api_key = str(self.config.get("gateway_api_key") or "")
        return {"X-API-Key": api_key} if api_key else {}

    async def retrieve(self, request: RetrieveRequest) -> RetrieveResponse:
        started = time.perf_counter()
        sources = await self._search(request.query, request.top_k, request.options)
        return RetrieveResponse(
            request_id=request.request_key(),
            method_id=self.method_id,
            sources=sources,
            latency_ms=(time.perf_counter() - started) * 1000,
            backend_metadata={"backend": "openviking_bot"},
        )

    async def chat(self, request: ChatRequest) -> ChatResponse:
        if self.config.get("gateway_mode", "external") == "disabled":
            raise HTTPException(status_code=503, detail="OpenViking bot API is disabled")
        started = time.perf_counter()
        payload = {
            "message": request.question,
            "session_id": request.session_id or request.request_key(),
            "user_id": request.user_id,
            "stream": False,
            "context": [
                {"role": item.role, "content": item.content}
                for item in request.history
                if item.role in {"user", "assistant", "system"}
            ]
            or None,
        }
        async with httpx.AsyncClient(timeout=None) as client:
            response = await client.post(
                f"{self.bot_base}/bot/v1/chat",
                json=payload,
                headers=self.headers(),
            )
        if response.status_code >= 400:
            raise HTTPException(status_code=response.status_code, detail=response.text)
        data = response.json()
        answer = data.get("message") or data.get("answer") or ""
        sources = _sources_from_events(data.get("events") or [])
        return ChatResponse(
            request_id=request.request_key(),
            method_id=self.method_id,
            session_id=data.get("session_id") or payload["session_id"],
            answer=answer,
            sources=sources,
            latency_ms=(time.perf_counter() - started) * 1000,
            backend_metadata={"backend": "openviking_bot", "events": data.get("events") or []},
        )

    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[bytes]:
        if self.config.get("gateway_mode", "external") == "disabled":
            yield _sse("error", {"message": "OpenViking bot API is disabled"})
            return
        payload = {
            "message": request.question,
            "session_id": request.session_id or request.request_key(),
            "user_id": request.user_id,
            "stream": True,
        }
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream(
                "POST",
                f"{self.bot_base}/bot/v1/chat/stream",
                json=payload,
                headers=self.headers(),
            ) as response:
                if response.status_code >= 400:
                    detail = await response.aread()
                    yield _sse("error", {"message": detail.decode("utf-8", errors="replace")})
                    return
                async for chunk in response.aiter_bytes():
                    yield chunk

    async def create_document(self, request: DocumentCreate) -> DocumentResponse:
        path = request.path or request.url
        if not path:
            raise HTTPException(status_code=400, detail="path or url is required")
        document_id = request.document_id or Path(path).stem or "openviking-document"
        started = time.perf_counter()
        try:
            configure_paths(self.config)
            from vikingbot.openviking_mount.ov_server import VikingClient

            client = await VikingClient.create(None)
            try:
                result = await self._add_resource(client, path, request)
            finally:
                close = getattr(client, "close", None)
                if close:
                    await close()
            result_payload = result if isinstance(result, dict) else {"result": result}
            wait = _bool_option(request.options, "wait", False)
            status = "completed" if wait else "submitted"
            if result_payload.get("status") == "error":
                status = "failed"
            return DocumentResponse(
                document_id=document_id,
                method_id=self.method_id,
                status=status,
                message=(
                    "resource processed by OpenViking"
                    if status == "completed"
                    else "resource submitted to OpenViking"
                ),
                metadata={
                    "result": result,
                    "task_id": result_payload.get("task_id"),
                    "root_uri": result_payload.get("root_uri"),
                    "queue_status": result_payload.get("queue_status"),
                    "telemetry": result_payload.get("telemetry"),
                    "wait": wait,
                    "elapsed_ms": (time.perf_counter() - started) * 1000,
                },
            )
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    async def list_ingestions(
        self,
        task_type: str | None = "add_resource",
        status: str | None = None,
        resource_id: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        try:
            configure_paths(self.config)
            from vikingbot.openviking_mount.ov_server import VikingClient

            client = await VikingClient.create(None)
            try:
                tasks = await client.client.list_tasks(
                    task_type=task_type,
                    status=status,
                    resource_id=resource_id,
                    limit=limit,
                )
            finally:
                close = getattr(client, "close", None)
                if close:
                    await close()
            return {"method_id": self.method_id, "tasks": tasks}
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    async def ingestion_status(self) -> dict[str, Any]:
        try:
            configure_paths(self.config)
            from vikingbot.openviking_mount.ov_server import VikingClient

            client = await VikingClient.create(None)
            try:
                tasks = await client.client.list_tasks(task_type="add_resource", limit=200)
                queue = await client.client._get_queue_status()
            finally:
                close = getattr(client, "close", None)
                if close:
                    await close()
            return {
                "method_id": self.method_id,
                "tasks": _task_summary(tasks),
                "queue": queue,
                "queue_counts": _parse_queue_counts(queue),
            }
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    async def get_ingestion(self, task_id: str) -> dict[str, Any]:
        try:
            configure_paths(self.config)
            from vikingbot.openviking_mount.ov_server import VikingClient

            client = await VikingClient.create(None)
            try:
                task = await client.client.get_task(task_id)
            finally:
                close = getattr(client, "close", None)
                if close:
                    await close()
            if not task:
                raise HTTPException(status_code=404, detail="ingestion task not found")
            return {"method_id": self.method_id, "task": task}
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    async def wait_ingestions(self, timeout: float | None = None) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            configure_paths(self.config)
            from vikingbot.openviking_mount.ov_server import VikingClient

            client = await VikingClient.create(None)
            try:
                result = await client.client.wait_processed(timeout=timeout)
            finally:
                close = getattr(client, "close", None)
                if close:
                    await close()
            return {
                "method_id": self.method_id,
                "result": result,
                "timeout": timeout,
                "elapsed_ms": (time.perf_counter() - started) * 1000,
            }
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    async def _add_resource(
        self,
        client: Any,
        path: str,
        request: DocumentCreate,
    ) -> dict[str, Any]:
        options = dict(request.options or {})
        args = options.get("args")
        processor_kwargs = options.get("processor_kwargs")
        if processor_kwargs is not None and not isinstance(processor_kwargs, dict):
            raise HTTPException(status_code=400, detail="options.processor_kwargs must be an object")
        if args is not None and not isinstance(args, dict):
            raise HTTPException(status_code=400, detail="options.args must be an object")

        wait = _bool_option(options, "wait", False)
        telemetry = options.get("telemetry", {"summary": True} if wait else False)
        args_payload = dict(args or {})
        if "build_index" in options:
            args_payload["build_index"] = _bool_option(options, "build_index", True)
        if "summarize" in options:
            args_payload["summarize"] = _bool_option(options, "summarize", False)
        if processor_kwargs:
            args_payload.update(processor_kwargs)
        return await client.client.add_resource(
            path=path,
            to=options.get("to") or options.get("target_uri"),
            parent=options.get("parent"),
            reason=str(
                options.get("reason")
                or request.metadata.get("reason")
                or request.title
                or ""
            ),
            instruction=str(options.get("instruction") or ""),
            wait=wait,
            timeout=_float_option(options, "timeout", None),
            strict=_bool_option(options, "strict", False),
            ignore_dirs=options.get("ignore_dirs"),
            include=options.get("include"),
            exclude=options.get("exclude"),
            directly_upload_media=_bool_option(options, "directly_upload_media", True),
            preserve_structure=options.get("preserve_structure"),
            watch_interval=_float_option(options, "watch_interval", 0.0) or 0.0,
            args=args_payload or None,
            telemetry=telemetry,
        )

    async def list_documents(self) -> dict[str, Any]:
        try:
            configure_paths(self.config)
            from vikingbot.openviking_mount.ov_server import VikingClient

            client = await VikingClient.create(None)
            try:
                entries = await client.list_resources(path="viking://resources/", recursive=True)
            finally:
                close = getattr(client, "close", None)
                if close:
                    await close()
            return {"method_id": self.method_id, "documents": entries}
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    async def delete_document(self, document_id: str) -> dict[str, Any]:
        raise HTTPException(
            status_code=501,
            detail="OpenViking Bot worker does not expose stable document deletion yet",
        )

    async def update_document(
        self, document_id: str, request: DocumentCreate
    ) -> DocumentResponse:
        raise HTTPException(
            status_code=501,
            detail="OpenViking Bot worker does not expose stable document update yet",
        )

    async def _search(
        self, query: str, top_k: int, options: dict[str, Any]
    ) -> list[Source]:
        configure_paths(self.config)
        from vikingbot.openviking_mount.ov_server import VikingClient

        target_uri = options.get("target_uri") or "viking://resources/"
        client = await VikingClient.create(None)
        try:
            result = await client.search(query=query, target_uri=target_uri, limit=top_k)
        finally:
            close = getattr(client, "close", None)
            if close:
                await close()
        resources = result.get("resources", result if isinstance(result, list) else [])
        return [_source_from_ov_item(item, index) for index, item in enumerate(resources or [])]

    async def _wait_bot_api(self) -> None:
        deadline = time.time() + 30
        while time.time() < deadline:
            health = await self.health()
            if health.get("bot_status_code") is not None:
                return
            await asyncio.sleep(0.5)
        raise RuntimeError("OpenViking bot API did not start")

    async def _start_server(self) -> None:
        ov_conf = self.config.get("ov_conf")
        if not ov_conf:
            raise RuntimeError("server_mode=managed requires ov_conf")
        server_url = self.server_base
        if await self._is_http_ready(f"{server_url}/health"):
            return
        if self.config.get("cleanup_on_start", True):
            ports = [int(self.config.get("server_port") or 1933)]
            if self._server_should_start_bot() and not self._uses_server_bot_proxy():
                ports.append(int(self.config.get("gateway_port") or 18790))
            cleanup_ports(ports)

        logs_dir = Path(str(self.config.get("logs_dir", "logs"))).expanduser()
        logs_dir.mkdir(parents=True, exist_ok=True)

        command = ["openviking-server", "--config", str(ov_conf)]
        if self.config.get("server_host"):
            command.extend(["--host", str(self.config["server_host"])])
        if self.config.get("server_port"):
            command.extend(["--port", str(self.config["server_port"])])
        if self._server_should_start_bot():
            command.append("--with-bot")
            if self.config.get("server_bot_port"):
                command.extend(["--bot-port", str(self.config["server_bot_port"])])
            command.extend(["--bot-log-dir", str(logs_dir)])

        stdout = open(logs_dir / f"{self.method_id}.openviking-server.out.log", "a", encoding="utf-8")
        stderr = open(logs_dir / f"{self.method_id}.openviking-server.err.log", "a", encoding="utf-8")
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        env["OPENVIKING_CONFIG_FILE"] = str(ov_conf)
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        self._server_process = subprocess.Popen(
            command,
            cwd=self._openviking_cwd(),
            env=env,
            stdout=stdout,
            stderr=stderr,
            creationflags=creationflags,
        )

        deadline = time.time() + float(self.config.get("server_startup_timeout", 90))
        while time.time() < deadline:
            if self._server_process.poll() is not None:
                raise RuntimeError(
                    f"openviking-server exited with code {self._server_process.returncode}"
                )
            if await self._is_http_ready(f"{server_url}/health"):
                if not self._uses_server_bot_proxy():
                    return
                bot_health = await self._check_http(f"{self.bot_base}/bot/v1/health")
                if bot_health["ok"]:
                    return
            await asyncio.sleep(0.5)
        raise RuntimeError("openviking-server did not start")

    def _bot_route_mode(self) -> str:
        mode = str(self.config.get("bot_route") or "").strip().lower()
        if mode in {"server", "gateway"}:
            return mode
        if self.config.get("server_with_bot") is False:
            return "gateway"
        if self.config.get("gateway_url") and self.config.get("server_with_bot") is not True:
            return "gateway"
        return "server"

    def _uses_server_bot_proxy(self) -> bool:
        if self.config.get("gateway_mode", "external") == "disabled":
            return False
        return self._bot_route_mode() == "server"

    def _server_should_start_bot(self) -> bool:
        if self.config.get("server_with_bot") is not None:
            return bool(self.config["server_with_bot"])
        return self.config.get("gateway_mode", "external") != "disabled"

    def _openviking_cwd(self) -> str | None:
        root = self.config.get("openviking_root")
        if not root:
            return None
        return str(Path(str(root)).expanduser().resolve())

    async def _check_http(self, url: str) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                response = await client.get(url)
            return {
                "ok": response.status_code < 500,
                "status_code": response.status_code,
                "response": _safe_json(response),
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    async def _is_http_ready(self, url: str) -> bool:
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                response = await client.get(url)
            return response.status_code < 500
        except Exception:
            return False


def create_app(config: dict[str, Any]) -> FastAPI:
    worker = OpenVikingBotWorker(config)
    app = FastAPI(
        title="YQ OpenViking Bot RAG Worker",
        description="Standard worker API wrapping Vikingbot OpenAPIChannel.",
        version=__version__,
    )

    @app.on_event("startup")
    async def startup() -> None:
        await worker.startup()

    @app.on_event("shutdown")
    async def shutdown() -> None:
        await worker.shutdown()

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return await worker.health()

    @app.get("/stats")
    async def stats() -> dict[str, Any]:
        return {"method_id": worker.method_id, "backend": "openviking_bot"}

    @app.post("/documents", response_model=DocumentResponse)
    async def documents(request: DocumentCreate) -> DocumentResponse:
        return await worker.create_document(request)

    @app.get("/documents")
    async def list_documents() -> dict[str, Any]:
        return await worker.list_documents()

    @app.post("/ingestions/wait")
    async def wait_ingestions(
        payload: dict[str, Any] | None = Body(default=None),
    ) -> dict[str, Any]:
        timeout = None
        if payload:
            timeout = _float_or_none(payload.get("timeout"))
        return await worker.wait_ingestions(timeout=timeout)

    @app.get("/ingestions")
    async def list_ingestions(
        task_type: str | None = Query(default="add_resource"),
        status: str | None = Query(default=None),
        resource_id: str | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=200),
    ) -> dict[str, Any]:
        return await worker.list_ingestions(
            task_type=task_type,
            status=status,
            resource_id=resource_id,
            limit=limit,
        )

    @app.get("/ingestions/status")
    async def ingestion_status() -> dict[str, Any]:
        return await worker.ingestion_status()

    @app.get("/ingestions/{task_id}")
    async def get_ingestion(task_id: str) -> dict[str, Any]:
        return await worker.get_ingestion(task_id)

    @app.patch("/documents/{document_id}", response_model=DocumentResponse)
    async def update_document(
        document_id: str, request: DocumentCreate
    ) -> DocumentResponse:
        return await worker.update_document(document_id, request)

    @app.delete("/documents/{document_id}")
    async def delete_document(document_id: str) -> dict[str, Any]:
        return await worker.delete_document(document_id)

    @app.post("/retrieve", response_model=RetrieveResponse)
    async def retrieve(request: RetrieveRequest) -> RetrieveResponse:
        return await worker.retrieve(request)

    @app.post("/chat", response_model=ChatResponse)
    async def chat(request: ChatRequest) -> ChatResponse:
        return await worker.chat(request)

    @app.post("/chat/stream")
    async def chat_stream(request: ChatRequest) -> StreamingResponse:
        return StreamingResponse(worker.stream_chat(request), media_type="text/event-stream")

    return app


def _source_from_ov_item(item: dict[str, Any], index: int) -> Source:
    uri = str(item.get("uri") or item.get("path") or f"openviking:{index}")
    return Source(
        source_id=uri,
        title=item.get("title") or item.get("name"),
        url=uri if uri.startswith(("http://", "https://", "viking://")) else None,
        snippet=item.get("abstract") or item.get("overview") or item.get("snippet"),
        score=_float_or_none(item.get("score")),
        metadata=item,
    )


def _sources_from_events(events: list[dict[str, Any]]) -> list[Source]:
    sources: list[Source] = []
    for event in events:
        data = event.get("data")
        if not isinstance(data, (dict, list)):
            continue
        text = json.dumps(data, ensure_ascii=False)
        if "viking://" not in text:
            continue
        sources.append(
            Source(
                source_id=f"event:{len(sources)}",
                snippet=text[:500],
                metadata={"event": event},
            )
        )
    return sources


def _safe_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except Exception:
        return response.text


def _task_summary(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {"pending": 0, "running": 0, "completed": 0, "failed": 0, "unknown": 0}
    for task in tasks or []:
        status = str(task.get("status") or "unknown").lower()
        counts[status if status in counts else "unknown"] += 1
    return {"total": len(tasks or []), "counts": counts, "items": tasks}


def _parse_queue_counts(queue: dict[str, Any]) -> dict[str, dict[str, int]]:
    status_text = ""
    if isinstance(queue, dict):
        result = queue.get("result") if isinstance(queue.get("result"), dict) else queue
        status_text = str(result.get("status") or "")
    counts: dict[str, dict[str, int]] = {}
    for line in status_text.splitlines():
        if not line.strip() or "|" not in line or "Queue" in line or "---" in line:
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 7:
            continue
        name = cells[0]
        values = [_int_or_zero(value) for value in cells[1:7]]
        if not name or name.upper() == "TOTAL":
            continue
        counts[name] = {
            "pending": values[0],
            "in_progress": values[1],
            "processed": values[2],
            "requeued": values[3],
            "errors": values[4],
            "total": values[5],
        }
    return counts


def _int_or_zero(value: Any) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return 0


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except Exception:
        return None


def _float_option(options: dict[str, Any], name: str, default: float | None) -> float | None:
    if name not in options or options.get(name) is None:
        return default
    return _float_or_none(options.get(name))


def _bool_option(options: dict[str, Any], name: str, default: bool) -> bool:
    if name not in options:
        return default
    value = options.get(name)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "no", "n", "off"}:
            return False
    return bool(value)


def _sse(event: str, data: Any) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8")
