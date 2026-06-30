from __future__ import annotations

import argparse

import uvicorn

from yq_rag_deepread.app import create_app
from yq_rag_deepread.config import load_worker_config


def main() -> None:
    parser = argparse.ArgumentParser(prog="deepread-worker")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18102)
    args = parser.parse_args()
    app = create_app(load_worker_config())
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
