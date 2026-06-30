from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn

from yq_rag_openviking_bot.app import create_app
from yq_rag_openviking_bot.config import load_worker_config
from yq_rag_openviking_bot.process_cleanup import cleanup_ports


def main() -> None:
    parser = argparse.ArgumentParser(prog="openviking-bot-worker")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18101)
    args = parser.parse_args()
    config = load_worker_config()
    source_ov_conf = config.get("source_ov_conf")
    if source_ov_conf:
        source_path = Path(str(source_ov_conf)).expanduser().resolve()
        print(f"[rag-openviking-bot] source ov.conf: {source_path}", flush=True)
        print(f"[rag-openviking-bot] source ov.conf dir: {source_path.parent}", flush=True)
    ov_conf = config.get("ov_conf")
    if ov_conf:
        ov_conf_path = Path(str(ov_conf)).expanduser().resolve()
        print(f"[rag-openviking-bot] runtime ov.conf: {ov_conf_path}", flush=True)
        print(f"[rag-openviking-bot] runtime ov.conf dir: {ov_conf_path.parent}", flush=True)
    else:
        print("[rag-openviking-bot] ov.conf: not found", flush=True)
    print(f"[rag-openviking-bot] worker bind: {args.host}:{args.port}", flush=True)
    print(f"[rag-openviking-bot] worker url: http://{args.host}:{args.port}", flush=True)
    print(f"[rag-openviking-bot] openviking server bind: {config.get('server_host')}:{config.get('server_port')}", flush=True)
    print(f"[rag-openviking-bot] openviking server url: {config.get('server_url')}", flush=True)
    print(f"[rag-openviking-bot] openviking server port auto: {config.get('server_port_auto')}", flush=True)
    if config.get("server_bot_port"):
        print(f"[rag-openviking-bot] openviking bot gateway port: {config.get('server_bot_port')}", flush=True)
        print(f"[rag-openviking-bot] openviking bot gateway port auto: {config.get('server_bot_port_auto')}", flush=True)
    print(f"[rag-openviking-bot] bot route: {config.get('bot_route')}", flush=True)
    if config.get("cleanup_on_start", True):
        cleanup_ports([args.port])
    app = create_app(config)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
