from __future__ import annotations

from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from yq_qa_rag.app import create_app
from yq_qa_rag.config import AppConfig
from yq_qa_rag.rag_manager_client import RagManagerClient


def make_config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        backend="openviking_rag",
        qa_db_path=str(tmp_path / "qa.sqlite3"),
        qa_rag_manager_base_url="http://rag-manager.test",
        auth_enabled=False,
    )


def test_method_runtime_actions_are_proxied_to_manager(tmp_path: Path) -> None:
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        suffix = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(
            200,
            json={
                "method_id": "method-a",
                "status": "running",
                "worker_url": "http://127.0.0.1:18100",
                "message": suffix,
            },
        )

    RagManagerClient.transport = httpx.MockTransport(handler)
    app = create_app(make_config(tmp_path))
    try:
        with TestClient(app) as client:
            for suffix in ["runtime", "health", "start", "stop", "restart"]:
                method = "GET" if suffix in {"runtime", "health"} else "POST"
                response = client.request(method, f"/v1/rag-methods/method-a/{suffix}")
                assert response.status_code == 200
                assert response.json()["method_id"] == "method-a"
    finally:
        RagManagerClient.transport = None

    assert calls == [
        ("GET", "/v1/rag-methods/method-a/runtime"),
        ("GET", "/v1/rag-methods/method-a/health"),
        ("POST", "/v1/rag-methods/method-a/start"),
        ("POST", "/v1/rag-methods/method-a/stop"),
        ("POST", "/v1/rag-methods/method-a/restart"),
    ]
