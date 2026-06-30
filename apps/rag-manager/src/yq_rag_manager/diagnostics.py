from __future__ import annotations

import json
from typing import Any


SENSITIVE_KEY_PARTS = (
    "api_key",
    "apikey",
    "access_key",
    "secret",
    "token",
    "password",
    "authorization",
    "credential",
)


def redacted(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "<redacted>" if _is_sensitive_key(str(key)) else redacted(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redacted(item) for item in value]
    return value


def print_json_block(title: str, value: Any, *, prefix: str = "[rag-manager]") -> None:
    print(f"{prefix} {title}:", flush=True)
    text = json.dumps(redacted(value), ensure_ascii=False, indent=2)
    for line in text.splitlines():
        print(f"{prefix}   {line}", flush=True)


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in SENSITIVE_KEY_PARTS)
