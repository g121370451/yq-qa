from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from yq_qa_rag.app import create_app
from yq_qa_rag.config import AppConfig


def make_config(tmp_path: Path, *, auth_enabled: bool) -> AppConfig:
    return AppConfig(
        backend="openviking_rag",
        qa_db_path=str(tmp_path / "qa.sqlite3"),
        auth_enabled=auth_enabled,
        auth_jwt_secret="test-secret",
        auth_admin_username="admin",
        auth_admin_password="secret-password",
    )


def test_config_is_public_when_auth_disabled(tmp_path: Path) -> None:
    app = create_app(make_config(tmp_path, auth_enabled=False))
    with TestClient(app) as client:
        response = client.get("/v1/config")
    assert response.status_code == 200


def test_protected_api_requires_token_when_auth_enabled(tmp_path: Path) -> None:
    app = create_app(make_config(tmp_path, auth_enabled=True))
    with TestClient(app) as client:
        response = client.get("/v1/config")
    assert response.status_code == 401
    assert response.json()["detail"] == "authentication required"


def test_login_returns_token_and_allows_protected_api(tmp_path: Path) -> None:
    app = create_app(make_config(tmp_path, auth_enabled=True))
    with TestClient(app) as client:
        login = client.post(
            "/v1/auth/login",
            json={"username": "admin", "password": "secret-password"},
        )
        assert login.status_code == 200
        token = login.json()["access_token"]

        me = client.get("/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200
        assert me.json()["username"] == "admin"
        assert me.json()["role"] == "admin"

        config = client.get("/v1/config", headers={"Authorization": f"Bearer {token}"})
        assert config.status_code == 200


def test_login_rejects_wrong_password(tmp_path: Path) -> None:
    app = create_app(make_config(tmp_path, auth_enabled=True))
    with TestClient(app) as client:
        response = client.post(
            "/v1/auth/login",
            json={"username": "admin", "password": "wrong"},
        )
    assert response.status_code == 401
