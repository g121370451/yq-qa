from __future__ import annotations

import argparse

import uvicorn

from yq_qa_rag.app import create_app
from yq_qa_rag.config import AppConfig, BackendName, load_env_file


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a unified RAG backend wrapper.")
    parser.add_argument(
        "--backend",
        choices=["openviking_rag", "openviking-rag", "deepread", "ovbot"],
        default=None,
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--env-file", default=None)
    parser.add_argument("--log-level", default="info")
    args = parser.parse_args()

    load_env_file(args.env_file)
    backend = args.backend.replace("-", "_") if args.backend is not None else None
    config = AppConfig.from_env(backend=backend)  # type: ignore[arg-type]
    default_port = 18791 if config.backend in {"openviking_rag", "ovbot"} else 18800
    app = create_app(config)

    uvicorn.run(
        app,
        host=args.host,
        port=args.port or default_port,
        log_level=args.log_level,
    )
