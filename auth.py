"""Авторизация (PLAN_multiuser v3 §6.3): сессии для юзеров, LAB_INGEST_TOKEN
только для админ-канала. user_id НИКОГДА не берётся из query/body юзерских
запросов — только из сессии (§3.2 п.2).
"""
import os
import secrets
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from fastapi import Cookie, Depends, Header, HTTPException, Request

import db
from security import token_hash

SESSION_COOKIE = "session"
SESSION_TTL_DAYS = 30


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _bearer_ok(authorization: Optional[str], expected: str) -> bool:
    if not authorization or not authorization.startswith("Bearer "):
        return False
    return secrets.compare_digest(authorization[len("Bearer "):], expected)


def require_admin_token(authorization: Optional[str] = Header(None)) -> None:
    """Админ-канал (/admin/*, /ingest/dictionary, admin-бэкфилл): LAB_INGEST_TOKEN.

    Fail closed: env не задан — всё закрыто.
    """
    expected = os.environ.get("LAB_INGEST_TOKEN", "")
    if not expected or not _bearer_ok(authorization, expected):
        raise HTTPException(status_code=401, detail="invalid or missing bearer token")


def get_conn():
    conn = db.connect()
    try:
        yield conn
    finally:
        conn.close()


def _session_user(conn: sqlite3.Connection, token: str) -> Optional[dict]:
    row = conn.execute(
        """SELECT s.token_hash AS s_token_hash, s.expires_at AS s_expires_at, u.*
           FROM sessions s JOIN users u ON u.user_id = s.user_id
           WHERE s.token_hash = ?""",
        (token_hash(token),),
    ).fetchone()
    if not row:
        return None
    d = dict(row)
    if d["s_expires_at"] <= _now_iso():
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (d["s_token_hash"],))
        conn.commit()
        return None
    if d["status"] != "active":
        return None
    conn.execute("UPDATE sessions SET last_seen = ? WHERE token_hash = ?",
                 (_now_iso(), d["s_token_hash"]))
    conn.commit()
    return d


def require_user(
    request: Request,
    session: Optional[str] = Cookie(None),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """Юзер из сессионной cookie (или Bearer-токена сессии — для тестов/скриптов).

    Возвращает dict строки users. 401 если сессии нет/протухла/юзер disabled.
    """
    token = session
    if not token:
        authz = request.headers.get("Authorization", "")
        if authz.startswith("Bearer "):
            token = authz[len("Bearer "):]
    if not token:
        raise HTTPException(status_code=401, detail="не авторизован")
    user = _session_user(conn, token)
    if not user:
        raise HTTPException(status_code=401, detail="сессия недействительна")
    return user


def require_admin_user(user: dict = Depends(require_user)) -> dict:
    """role=admin поверх обычной сессии (admin-вкладка дэша)."""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="нужны права администратора")
    return user


def create_session(conn: sqlite3.Connection, user_id: str, token: str,
                   ttl_days: int = SESSION_TTL_DAYS) -> None:
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    conn.execute(
        "INSERT INTO sessions (token_hash, user_id, created_at, expires_at, last_seen)"
        " VALUES (?, ?, ?, ?, ?)",
        (token_hash(token), user_id,
         now.strftime("%Y-%m-%dT%H:%M:%SZ"),
         (now + timedelta(days=ttl_days)).strftime("%Y-%m-%dT%H:%M:%SZ"),
         now.strftime("%Y-%m-%dT%H:%M:%SZ")),
    )
    conn.commit()


def drop_session(conn: sqlite3.Connection, token: str) -> None:
    conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash(token),))
    conn.commit()
