from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any


def load_worker_config() -> dict[str, Any]:
    raw = os.getenv("YQ_RAG_METHOD_CONFIG", "{}")
    config = json.loads(raw)
    method_id = os.getenv("YQ_RAG_METHOD_ID")
    if method_id:
        config.setdefault("method_id", method_id)
    return config


def configure_paths(config: dict[str, Any]) -> None:
    """Add only optional compatibility paths.

    DeepRead itself is packaged inside this uv subproject under src/DeepRead.
    `deepread_source_path` is kept as a fallback for local modules that are not
    part of DeepRead, such as the historical ov_test helpers.
    """
    project_value = config.get("deepread_source_path")
    if not project_value:
        return
    project = Path(str(project_value)).expanduser().resolve()
    for path in (project / "ov_test",):
        text = str(path)
        if path.exists() and text not in sys.path:
            sys.path.insert(0, text)
