from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import Depends, HTTPException, Query, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from yq_qa_rag.config import AppConfig
from yq_qa_rag.models import AuthUser


@dataclass(slots=True)
class StoredUser:
    user_id: str
    username: str
    password_hash: str
    display_name: str | None
    role: str
    enabled: bool

    def public(self) -> AuthUser:
        return AuthUser(
            user_id=self.user_id,
            username=self.username,
            display_name=self.display_name,
            role=self.role,
        )


class UserStore:
    def __init__(self, db_path: str) -> None:
        if db_path == ":memory:":
            self.db_path = db_path
        else:
            self.db_path = str(Path(db_path).expanduser().resolve())
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._migrate()

    def close(self) -> None:
        self._conn.close()

    def _migrate(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                display_name TEXT,
                role TEXT NOT NULL DEFAULT 'user',
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_login_at TEXT
            );
            """
        )
        self._conn.commit()

    def count_users(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()
        return int(row["c"])

    def ensure_admin(self, config: AppConfig) -> StoredUser | None:
        if self.count_users() > 0:
            return None
        if not config.auth_admin_password:
            if config.auth_enabled:
                raise RuntimeError(
                    "YQ_QA_ADMIN_PASSWORD is required when auth is enabled and users table is empty"
                )
            return None
        return self.create_user(
            username=config.auth_admin_username,
            password=config.auth_admin_password,
            display_name=config.auth_admin_display_name,
            role="admin",
        )

    def create_user(
        self,
        *,
        username: str,
        password: str,
        display_name: str | None = None,
        role: str = "user",
        enabled: bool = True,
    ) -> StoredUser:
        now = _utcnow_iso()
        user_id = str(uuid4())
        password_hash = hash_password(password)
        self._conn.execute(
            """
            INSERT INTO users (
                user_id, username, password_hash, display_name, role,
                enabled, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                username,
                password_hash,
                display_name,
                role,
                1 if enabled else 0,
                now,
                now,
            ),
        )
        self._conn.commit()
        return self.get_user_by_username(username)

    def get_user_by_username(self, username: str) -> StoredUser:
        row = self._conn.execute(
            "SELECT * FROM users WHERE username = ?",
            (username,),
        ).fetchone()
        if row is None:
            raise KeyError(username)
        return _stored_user(row)

    def get_user(self, user_id: str) -> StoredUser:
        row = self._conn.execute(
            "SELECT * FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        if row is None:
            raise KeyError(user_id)
        return _stored_user(row)

    def authenticate(self, username: str, password: str) -> StoredUser | None:
        try:
            user = self.get_user_by_username(username)
        except KeyError:
            return None
        if not user.enabled or not verify_password(password, user.password_hash):
            return None
        self._conn.execute(
            "UPDATE users SET last_login_at = ?, updated_at = ? WHERE user_id = ?",
            (_utcnow_iso(), _utcnow_iso(), user.user_id),
        )
        self._conn.commit()
        return user


class TokenService:
    def __init__(self, secret: str, expire_minutes: int) -> None:
        if not secret:
            raise RuntimeError("YQ_QA_JWT_SECRET is required when auth is enabled")
        self.secret = secret.encode("utf-8")
        self.expire_minutes = max(1, expire_minutes)

    def issue(self, user: StoredUser) -> tuple[str, datetime]:
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=self.expire_minutes)
        payload = {
            "sub": user.user_id,
            "username": user.username,
            "role": user.role,
            "exp": int(expires_at.timestamp()),
        }
        return self._encode(payload), expires_at

    def verify(self, token: str) -> dict[str, Any]:
        try:
            payload_b64, signature_b64 = token.split(".", 1)
        except ValueError as exc:
            raise ValueError("invalid token") from exc
        expected = _b64encode(hmac.new(self.secret, payload_b64.encode("utf-8"), hashlib.sha256).digest())
        if not hmac.compare_digest(signature_b64, expected):
            raise ValueError("invalid token signature")
        payload = json.loads(_b64decode(payload_b64).decode("utf-8"))
        if int(payload.get("exp") or 0) < int(datetime.now(timezone.utc).timestamp()):
            raise ValueError("token expired")
        return payload

    def _encode(self, payload: dict[str, Any]) -> str:
        payload_b64 = _b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        signature = hmac.new(self.secret, payload_b64.encode("utf-8"), hashlib.sha256).digest()
        return f"{payload_b64}.{_b64encode(signature)}"


class AuthContext:
    def __init__(self, config: AppConfig, user_store: UserStore) -> None:
        self.enabled = config.auth_enabled
        self.user_store = user_store
        self.token_service = (
            TokenService(config.auth_jwt_secret, config.auth_token_expire_minutes)
            if config.auth_enabled
            else None
        )

    def issue_token(self, user: StoredUser) -> tuple[str, datetime]:
        if self.token_service is None:
            raise RuntimeError("auth is disabled")
        return self.token_service.issue(user)

    def verify_token(self, token: str) -> AuthUser:
        if self.token_service is None:
            raise ValueError("auth is disabled")
        payload = self.token_service.verify(token)
        user = self.user_store.get_user(str(payload["sub"]))
        if not user.enabled:
            raise ValueError("user disabled")
        return user.public()


security = HTTPBearer(auto_error=False)


def auth_context(request: Request) -> AuthContext:
    return request.app.state.auth_context


async def require_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    access_token: str | None = Query(default=None),
) -> AuthUser | None:
    context: AuthContext = request.app.state.auth_context
    if not context.enabled:
        return None
    token = access_token
    if credentials and credentials.scheme.lower() == "bearer":
        token = credentials.credentials
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="authentication required",
        )
    try:
        return context.verify_token(token)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or expired token",
        ) from exc


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 120_000)
    return f"pbkdf2_sha256$120000${_b64encode(salt)}${_b64encode(digest)}"


def verify_password(password: str, password_hash: str) -> bool:
    try:
        algorithm, iterations_raw, salt_b64, digest_b64 = password_hash.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        iterations = int(iterations_raw)
        salt = _b64decode(salt_b64)
        expected = _b64decode(digest_b64)
    except Exception:
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual, expected)


def _stored_user(row: sqlite3.Row) -> StoredUser:
    return StoredUser(
        user_id=row["user_id"],
        username=row["username"],
        password_hash=row["password_hash"],
        display_name=row["display_name"],
        role=row["role"],
        enabled=bool(row["enabled"]),
    )


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)
