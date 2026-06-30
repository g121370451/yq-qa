from __future__ import annotations

import json
import os
import re
import socket
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


def default_ov_conf() -> str | None:
    candidates: list[Path] = []
    env_path = os.getenv("OPENVIKING_CONFIG_FILE")
    if env_path:
        candidates.append(Path(env_path).expanduser())

    candidates.extend(
        [
            Path.cwd() / "ov.conf",
            Path(__file__).resolve().parents[2] / "ov.conf",
            Path.home() / ".openviking" / "ov.conf",
        ]
    )
    for path in candidates:
        if path.exists():
            return str(path.resolve())
    return None


def load_worker_config() -> dict[str, Any]:
    raw = os.getenv("YQ_RAG_METHOD_CONFIG", "{}")
    config = json.loads(raw)
    method_id = os.getenv("YQ_RAG_METHOD_ID")
    if method_id:
        config.setdefault("method_id", method_id)
    ov_conf = default_ov_conf()
    if ov_conf:
        config.setdefault("ov_conf", ov_conf)
    config.setdefault("server_mode", "managed")
    config.setdefault("server_with_bot", True)
    config.setdefault("bot_route", "server")
    _apply_server_defaults(config)
    prepare_runtime_ov_conf(config)
    return config


def _apply_server_defaults(config: dict[str, Any]) -> None:
    server = _load_ov_server_config(config.get("ov_conf"))
    server_host = str(config.get("server_host") or server.get("host") or "127.0.0.1")
    server_port = _int_or_none(config.get("server_port"))
    if server_port is None:
        server_port = _port_from_url(config.get("server_url") or config.get("openviking_server_url"))
    if server_port is None:
        server_port = _int_or_none(server.get("port"))
    if server_port is None and config.get("server_mode", "managed") == "managed":
        server_port = _find_free_tcp_port(
            server_host,
            start=int(config.get("server_port_base") or os.getenv("YQ_RAG_OPENVIKING_SERVER_PORT_BASE", "20100")),
            count=int(config.get("server_port_range") or os.getenv("YQ_RAG_OPENVIKING_SERVER_PORT_RANGE", "1000")),
        )
        config["server_port_auto"] = True
    elif server_port is None:
        server_port = 1933
        config["server_port_auto"] = False
    else:
        config["server_port_auto"] = False
    config.setdefault("server_host", server_host)
    config.setdefault("server_port", server_port)
    url_host = "127.0.0.1" if server_host in {"0.0.0.0", "::", "all"} else server_host
    config.setdefault("server_url", f"http://{url_host}:{server_port}")
    _apply_bot_port_defaults(config, server_host)


def _apply_bot_port_defaults(config: dict[str, Any], host: str) -> None:
    if config.get("server_mode", "managed") != "managed":
        return
    if not bool(config.get("server_with_bot", True)):
        return
    bot_port = _int_or_none(config.get("server_bot_port") or config.get("bot_port"))
    if bot_port is None:
        bot_port = _find_free_tcp_port(
            host,
            start=int(config.get("server_bot_port_base") or os.getenv("YQ_RAG_OPENVIKING_BOT_PORT_BASE", "21100")),
            count=int(config.get("server_bot_port_range") or os.getenv("YQ_RAG_OPENVIKING_BOT_PORT_RANGE", "1000")),
        )
        config["server_bot_port_auto"] = True
    else:
        config["server_bot_port_auto"] = False
    config.setdefault("server_bot_port", bot_port)


def _int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _port_from_url(value: Any) -> int | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return urlparse(value).port
    except ValueError:
        return None


def _find_free_tcp_port(host: str, *, start: int, count: int) -> int:
    bind_host = "127.0.0.1" if host in {"0.0.0.0", "::", "all"} else host
    for port in range(start, start + count):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.2)
            if sock.connect_ex((bind_host, port)) != 0:
                return port
    raise RuntimeError(f"no free OpenViking server port found in {start}-{start + count - 1}")


def _load_ov_server_config(path_value: Any) -> dict[str, Any]:
    if not path_value:
        return {}
    path = Path(str(path_value)).expanduser()
    if not path.exists():
        return {}
    try:
        data = json.loads(_resolve_env_text(path.read_text(encoding="utf-8")))
    except Exception:
        return {}
    server = data.get("server", {}) if isinstance(data, dict) else {}
    return server if isinstance(server, dict) else {}


def prepare_runtime_ov_conf(config: dict[str, Any]) -> None:
    if config.get("runtime_ov_conf_generated"):
        return
    if config.get("server_mode", "managed") != "managed":
        return
    ov_conf = config.get("ov_conf")
    if not ov_conf:
        return

    source_path = Path(str(ov_conf)).expanduser().resolve()
    if not source_path.exists():
        return

    try:
        data = json.loads(_resolve_env_text(source_path.read_text(encoding="utf-8")))
    except Exception:
        return
    if not isinstance(data, dict):
        return

    server_data = data.get("server")
    if not isinstance(server_data, dict):
        server_data = {}
        data["server"] = server_data
    server_data["host"] = str(config.get("server_host") or "127.0.0.1")
    server_data["port"] = int(config.get("server_port") or 1933)

    method_id = str(config.get("method_id") or "openviking-bot")
    runtime_path_value = config.get("runtime_ov_conf")
    if runtime_path_value:
        runtime_path = Path(str(runtime_path_value)).expanduser()
    else:
        runtime_dir = Path(str(config.get("logs_dir") or "logs")).expanduser() / "runtime"
        runtime_path = runtime_dir / f"{method_id}.ov.conf"
    runtime_path = runtime_path.resolve()
    runtime_path.parent.mkdir(parents=True, exist_ok=True)
    runtime_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    config["source_ov_conf"] = str(source_path)
    config["ov_conf"] = str(runtime_path)
    config["runtime_ov_conf"] = str(runtime_path)
    config["runtime_ov_conf_generated"] = True


def configure_paths(config: dict[str, Any]) -> None:
    prepare_runtime_ov_conf(config)
    # Optional local-development override. Production deployments should install
    # openviking through uv instead of relying on an absolute source checkout.
    root = config.get("openviking_root")
    if root:
        root_path = Path(str(root)).expanduser().resolve()
        bot_path = root_path / "bot"
        for path in (str(root_path), str(bot_path)):
            if path not in sys.path:
                sys.path.insert(0, path)
    ov_conf = config.get("ov_conf")
    if ov_conf:
        os.environ["OPENVIKING_CONFIG_FILE"] = str(Path(str(ov_conf)).expanduser())


def _resolve_env_text(raw: str) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        value = os.getenv(name)
        if value is None:
            raise ValueError(f"environment variable is not set: {name}")
        return value

    return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", replace, raw)
