from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml


def load_config(path: str, env_file: str | None = None) -> dict[str, Any]:
    config_path = Path(path).expanduser().resolve()
    _load_dotenv(config_path, env_file=env_file)
    raw = _resolve_env_text(config_path.read_text(encoding="utf-8"))
    data = yaml.safe_load(raw) or {}
    _check_required_env(data.get("required_env") or [])
    data = _resolve_env(data)
    base = config_path.parent
    for section_name in ("dataset", "output", "ingestion"):
        section = data.get(section_name)
        if isinstance(section, dict):
            for key, value in list(section.items()):
                if key.endswith("_path") or key.endswith("_dir") or key.endswith("_root"):
                    section[key] = _resolve_path(value, base)
    ingestion_options = ((data.get("ingestion") or {}).get("options") or {})
    if isinstance(ingestion_options, dict) and isinstance(ingestion_options.get("output"), str):
        ingestion_options["output"] = _resolve_path(ingestion_options["output"], base)
    extra_documents = (data.get("ingestion") or {}).get("extra_documents") or []
    if isinstance(extra_documents, list):
        for item in extra_documents:
            if isinstance(item, dict) and isinstance(item.get("path"), str):
                item["path"] = _resolve_path(item["path"], base)
    method_config = ((data.get("rag_manager") or {}).get("method_config") or {})
    if isinstance(method_config, dict):
        for key, value in list(method_config.items()):
            if (
                key.endswith("_path")
                or key.endswith("_dir")
                or key in {"ov_conf", "logs_dir", "corpus_output_dir", "log_dir"}
            ):
                method_config[key] = _resolve_path(value, base)
    return data


def _load_dotenv(config_path: Path, *, env_file: str | None = None) -> None:
    for path in _dotenv_candidates(config_path, env_file=env_file):
        if not path.exists() or not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            parsed = _parse_dotenv_line(line)
            if parsed is None:
                continue
            key, value = parsed
            os.environ.setdefault(key, value)


def _dotenv_candidates(config_path: Path, *, env_file: str | None = None) -> list[Path]:
    explicit = env_file or os.getenv("YQ_RAG_MANAGER_EVAL_ENV_FILE")
    if explicit:
        return [Path(explicit).expanduser().resolve()]

    candidates = [
        Path.cwd() / ".env",
        Path.cwd() / "apps" / "rag-manager-eval" / ".env",
        config_path.parent / ".env",
        config_path.parent.parent / ".env",
    ]
    seen: set[Path] = set()
    unique: list[Path] = []
    for path in candidates:
        resolved = path.expanduser().resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(resolved)
    return unique


def _parse_dotenv_line(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None
    key, value = stripped.split("=", 1)
    key = key.strip()
    if key.startswith("export "):
        key = key.removeprefix("export ").strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
        return None

    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        quote = value[0]
        value = value[1:-1]
        if quote == '"':
            value = (
                value.replace(r"\n", "\n")
                .replace(r"\r", "\r")
                .replace(r"\t", "\t")
                .replace(r"\"", '"')
                .replace(r"\\", "\\")
            )
    else:
        value = _strip_dotenv_comment(value).strip()
    return key, value


def _strip_dotenv_comment(value: str) -> str:
    for index, char in enumerate(value):
        if char == "#" and (index == 0 or value[index - 1].isspace()):
            return value[:index]
    return value


def _check_required_env(names: Any) -> None:
    if not names:
        return
    if isinstance(names, str):
        names = [names]
    missing = [str(name) for name in names if not os.getenv(str(name))]
    if missing:
        joined = ", ".join(missing)
        raise ValueError(f"required environment variables are not set: {joined}")


def _resolve_path(value: Any, base: Path) -> Any:
    if not isinstance(value, str) or not value:
        return value
    rendered = value.format(cwd=str(Path.cwd()))
    path = Path(rendered).expanduser()
    if path.is_absolute():
        return str(path)
    return str((base / path).resolve())


def _resolve_env_text(raw: str) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        value = os.getenv(name)
        if value is None:
            raise ValueError(f"environment variable is not set: {name}")
        return value

    return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", replace, raw)


def _resolve_env(obj: Any) -> Any:
    if isinstance(obj, str):
        def replace(match: re.Match[str]) -> str:
            name = match.group(1)
            value = os.getenv(name)
            if value is None:
                raise ValueError(f"environment variable is not set: {name}")
            return value

        return re.sub(r"\$\{(\w+)\}", replace, obj)
    if isinstance(obj, list):
        return [_resolve_env(item) for item in obj]
    if isinstance(obj, dict):
        return {key: _resolve_env(value) for key, value in obj.items()}
    return obj
