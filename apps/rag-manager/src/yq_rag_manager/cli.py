from __future__ import annotations

import argparse
import os
from pathlib import Path

import uvicorn

from yq_rag_manager.app import create_app
from yq_rag_manager.diagnostics import print_json_block
from yq_rag_manager.store import Store


APP_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = APP_ROOT / "data" / "rag-manager.sqlite3"
DEFAULT_LOGS_DIR = APP_ROOT / "logs"


def main() -> None:
    parser = argparse.ArgumentParser(prog="rag-manager")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18081)
    parser.add_argument("--db", default=os.getenv("YQ_RAG_MANAGER_DB") or str(DEFAULT_DB_PATH))
    parser.add_argument("--logs-dir", default=os.getenv("YQ_RAG_MANAGER_LOGS_DIR") or str(DEFAULT_LOGS_DIR))
    parser.add_argument("--worker-host", default="127.0.0.1")
    parser.add_argument("--worker-base-port", type=int, default=18100)
    args = parser.parse_args()

    db_path = str(Path(args.db).expanduser().resolve())
    logs_dir = str(Path(args.logs_dir).expanduser().resolve())
    methods = Store(db_path).list_methods()
    print_json_block(
        "startup config",
        {
            "manager": {
                "host": args.host,
                "port": args.port,
                "url": f"http://{args.host}:{args.port}",
                "db": db_path,
                "logs_dir": logs_dir,
            },
            "workers": {
                "host": args.worker_host,
                "base_port": args.worker_base_port,
                "port_range": [args.worker_base_port, args.worker_base_port + 999],
            },
            "registered_methods": [
                {
                    "method_id": method.method_id,
                    "backend_type": method.backend_type,
                    "status": method.status,
                    "worker_url": method.worker_url,
                    "worker_port": method.worker_port,
                    "pid": method.pid,
                    "enabled": method.enabled,
                }
                for method in methods
            ],
        },
    )

    app = create_app(
        db_path=db_path,
        logs_dir=logs_dir,
        worker_host=args.worker_host,
        worker_base_port=args.worker_base_port,
    )
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
