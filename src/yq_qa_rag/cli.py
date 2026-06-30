from __future__ import annotations

import argparse
import json
from pathlib import Path

import uvicorn

from yq_qa_rag.app import create_app
from yq_qa_rag.config import AppConfig, BackendName, load_env_file


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the YQ-QA backend.")
    parser.add_argument(
        "--backend",
        choices=["openviking_rag", "openviking-rag", "deepread", "ovbot"],
        default=None,
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--env-file", default=None)
    parser.add_argument("--db", default=None)
    parser.add_argument("--manager-url", default=None, help="Optional initial rag-manager URL.")
    parser.add_argument("--log-level", default="info")
    args = parser.parse_args()

    load_env_file(args.env_file)
    backend = args.backend.replace("-", "_") if args.backend is not None else None
    config = AppConfig.from_env(backend=backend)  # type: ignore[arg-type]
    if args.db:
        config.qa_db_path = str(Path(args.db).expanduser().resolve())
    if args.manager_url:
        config.qa_rag_manager_base_url = args.manager_url.rstrip("/")
    port = args.port or 18082
    print(
        "[rag-server] startup config\n"
        + json.dumps(
            {
                "service_url": f"http://{args.host}:{port}",
                "swagger_url": f"http://{args.host}:{port}/docs",
                "note": "Runtime QA settings are stored in SQLite and can be updated by the frontend via /v1/config.",
                **config.public_dict(),
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    app = create_app(config)

    uvicorn.run(
        app,
        host=args.host,
        port=port,
        log_level=args.log_level,
    )
