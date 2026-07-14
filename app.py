"""health-multi — мультитенантный сервис хранения (PLAN_multiuser v3).

Авторизация по путям (§6.3): юзерские данные — сессия; бот — webhook_secret из
пути /tg/webhook/{user_id}; админ-канал — LAB_INGEST_TOKEN или сессия role=admin.
user_id никогда не приходит из query/body юзерских запросов.
"""
import csv
import io
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import yaml
from contextlib import asynccontextmanager
from fastapi import (Body, Depends, FastAPI, File, Header, HTTPException, Query,
                     Request, Response, UploadFile)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

import db
import telegram
import security
import authbot
from auth import (SESSION_COOKIE, create_session, drop_session, get_conn,
                  require_admin_token, require_admin_user, require_user)

# --- Квоты (M1: 50 PDF/день, 25 МБ/файл) -------------------------------------
MAX_PDF_BYTES = 25 * 1024 * 1024
PDF_DAILY_QUOTA = 50
INVITE_TTL_DAYS_DEFAULT = 7
BIND_CODE_TTL_MIN = 15


def _bootstrap_admin() -> None:
    """Первый админ из env (ADMIN_LOGIN/ADMIN_PASSWORD) при пустой таблице users.

    Дальше инвайты создаёт сам админ; env можно убрать после первого старта.
    """
    login = (os.environ.get("ADMIN_LOGIN") or "").strip().lower()
    password = os.environ.get("ADMIN_PASSWORD") or ""
    if not login or not password:
        return
    conn = db.connect()
    try:
        if conn.execute("SELECT 1 FROM users WHERE role = 'admin' LIMIT 1").fetchone():
            return
        if conn.execute("SELECT 1 FROM users WHERE login = ?", (login,)).fetchone():
            return
        conn.execute(
            "INSERT INTO users (user_id, login, name, password_hash, role, status, created_at)"
            " VALUES (?, ?, ?, ?, 'admin', 'active', ?)",
            (uuid.uuid4().hex, login, "Admin", security.hash_password(password), _now()),
        )
        conn.commit()
    finally:
        conn.close()


@asynccontextmanager
async def _lifespan(app: FastAPI):
    db.init_db()
    _bootstrap_admin()
    yield


app = FastAPI(title="health-multi", version="2.0.0", lifespan=_lifespan)

_cors = [o.strip() for o in os.environ.get("CORS_ORIGINS", "").split(",") if o.strip()]
if _cors:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
        allow_credentials=True,
    )


@app.middleware("http")
async def _no_store(request: Request, call_next):
    """§3.2 п.5: запрет кэширования на транспортном слое — против утечки через CDN."""
    response = await call_next(request)
    response.headers["Cache-Control"] = "private, no-store"
    return response


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _cookie_secure() -> bool:
    return os.environ.get("COOKIE_SECURE", "1") != "0"


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE, token, httponly=True, samesite="lax",
        secure=_cookie_secure(), max_age=30 * 24 * 3600, path="/",
    )


def _public_url() -> str:
    return os.environ.get("PUBLIC_URL", "").rstrip("/")


def _user_bot_token(user: dict) -> Optional[str]:
    enc = user.get("bot_token_enc")
    if not enc:
        return None
    return security.decrypt_bot_token(enc)


# ---------------------------------------------------------------------------
# Auth: регистрация по инвайту, логин, сессии (§6.1–6.2)
# ---------------------------------------------------------------------------

@app.post("/auth/register")
def auth_register(
    response: Response,
    payload: dict = Body(...),
    conn: sqlite3.Connection = Depends(get_conn),
):
    """Регистрация по инвайт-коду: имя, логин, пароль, consent. TG ID не запрашивается (§4)."""
    code = (payload.get("code") or "").strip()
    login = (payload.get("login") or "").strip().lower()
    name = (payload.get("name") or "").strip()
    password = payload.get("password") or ""
    consent = bool(payload.get("consent"))
    if not code or not login or not name or not password:
        raise HTTPException(status_code=400, detail="нужны code, login, name, password")
    if not consent:
        raise HTTPException(status_code=400, detail="нужно согласие с условиями")
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="пароль短: минимум 8 символов")
    inv = conn.execute(
        "SELECT * FROM invites WHERE code = ? AND kind = 'invite'", (code,)
    ).fetchone()
    if not inv or inv["used_by"]:
        raise HTTPException(status_code=400, detail="инвайт не найден или уже использован")
    if inv["expires_at"] and inv["expires_at"] <= _now():
        raise HTTPException(status_code=400, detail="инвайт истёк")
    if conn.execute("SELECT 1 FROM users WHERE login = ?", (login,)).fetchone():
        raise HTTPException(status_code=409, detail="логин занят")

    user_id = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO users (user_id, login, name, password_hash, role, status, created_at)"
        " VALUES (?, ?, ?, ?, 'user', 'active', ?)",
        (user_id, login, name, security.hash_password(password), _now()),
    )
    conn.execute("UPDATE invites SET used_by = ? WHERE code = ?", (user_id, code))
    conn.commit()

    token = security.new_token()
    create_session(conn, user_id, token)
    _set_session_cookie(response, token)
    return {"ok": True, "user_id": user_id, "login": login, "name": name}


@app.post("/auth/login")
def auth_login(
    request: Request,
    response: Response,
    payload: dict = Body(...),
    conn: sqlite3.Connection = Depends(get_conn),
):
    login = (payload.get("login") or "").strip().lower()
    password = payload.get("password") or ""
    ip = (request.client.host if request.client else "?")
    for key in (f"ip:{ip}", f"login:{login}"):
        wait = security.login_blocked_for(key)
        if wait > 0:
            raise HTTPException(status_code=429,
                                detail=f"слишком много попыток, подожди {int(wait) + 1} с")
    row = conn.execute("SELECT * FROM users WHERE login = ?", (login,)).fetchone()
    if not row or not security.verify_password(password, row["password_hash"]) \
            or row["status"] != "active":
        security.login_failed(f"ip:{ip}")
        security.login_failed(f"login:{login}")
        raise HTTPException(status_code=401, detail="неверный логин или пароль")
    security.login_succeeded(f"ip:{ip}")
    security.login_succeeded(f"login:{login}")
    token = security.new_token()
    create_session(conn, row["user_id"], token)
    _set_session_cookie(response, token)
    return {"ok": True, "login": login, "name": row["name"], "role": row["role"]}


@app.post("/auth/logout")
def auth_logout(
    request: Request,
    response: Response,
    conn: sqlite3.Connection = Depends(get_conn),
):
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        authz = request.headers.get("Authorization", "")
        token = authz[len("Bearer "):] if authz.startswith("Bearer ") else None
    if token:
        drop_session(conn, token)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@app.get("/auth/me")
def auth_me(user: dict = Depends(require_user)):
    return {
        "login": user["login"], "name": user["name"], "role": user["role"],
        "bot": {
            "connected": bool(user.get("bot_token_enc")),
            "username": user.get("bot_username"),
            "bound": bool(user.get("tg_user_id")),
        },
    }


@app.post("/auth/change_password")
def auth_change_password(
    payload: dict = Body(...),
    user: dict = Depends(require_user),
    conn: sqlite3.Connection = Depends(get_conn),
):
    old, new = payload.get("old") or "", payload.get("new") or ""
    if not security.verify_password(old, user["password_hash"]):
        raise HTTPException(status_code=403, detail="старый пароль неверен")
    if len(new) < 8:
        raise HTTPException(status_code=400, detail="пароль короткий: минимум 8 символов")
    conn.execute("UPDATE users SET password_hash = ? WHERE user_id = ?",
                 (security.hash_password(new), user["user_id"]))
    conn.commit()
    return {"ok": True}


@app.post("/auth/reset_password")
def auth_reset_password(
    response: Response,
    payload: dict = Body(...),
    conn: sqlite3.Connection = Depends(get_conn),
):
    """Сброс по reset-коду от админа (email-канала нет, §6.2)."""
    code = (payload.get("code") or "").strip()
    new = payload.get("new") or ""
    if len(new) < 8:
        raise HTTPException(status_code=400, detail="пароль короткий: минимум 8 символов")
    inv = conn.execute(
        "SELECT * FROM invites WHERE code = ? AND kind = 'reset'", (code,)
    ).fetchone()
    if not inv or inv["used_by"] or not inv["for_user"]:
        raise HTTPException(status_code=400, detail="код не найден или уже использован")
    if inv["expires_at"] and inv["expires_at"] <= _now():
        raise HTTPException(status_code=400, detail="код истёк")
    conn.execute("UPDATE users SET password_hash = ? WHERE user_id = ?",
                 (security.hash_password(new), inv["for_user"]))
    conn.execute("UPDATE invites SET used_by = ? WHERE code = ?", (inv["for_user"], code))
    # все старые сессии юзера гасим
    conn.execute("DELETE FROM sessions WHERE user_id = ?", (inv["for_user"],))
    conn.commit()
    token = security.new_token()
    create_session(conn, inv["for_user"], token)
    _set_session_cookie(response, token)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Управление ботом (§5.2): подключение токена, bind-код, статус, отключение
# ---------------------------------------------------------------------------

@app.post("/bot/connect")
def bot_connect(
    payload: dict = Body(...),
    user: dict = Depends(require_user),
    conn: sqlite3.Connection = Depends(get_conn),
):
    token = (payload.get("token") or "").strip()
    if not token or ":" not in token:
        raise HTTPException(status_code=400, detail="это не похоже на токен BotFather")
    dup = conn.execute(
        "SELECT user_id FROM users WHERE bot_username IS NOT NULL AND bot_token_enc IS NOT NULL"
    ).fetchall()
    try:
        me = telegram.get_me(token)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"токен не прошёл проверку getMe: {e}")
    bot_username = me.get("username") or ""
    for r in dup:
        if r["user_id"] != user["user_id"]:
            other = conn.execute("SELECT bot_username FROM users WHERE user_id = ?",
                                 (r["user_id"],)).fetchone()
            if other and other["bot_username"] == bot_username:
                raise HTTPException(status_code=409,
                                    detail="этот бот уже подключён к другому аккаунту")
    public = _public_url()
    if not public:
        raise HTTPException(status_code=503, detail="PUBLIC_URL не задан")
    webhook_secret = security.new_token(24)
    try:
        telegram.set_webhook(token, f"{public}/tg/webhook/{user['user_id']}", webhook_secret)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"setWebhook не прошёл: {e}")
    try:
        telegram.set_my_commands(token, [
            {"command": "start", "description": "привязать аккаунт"},
            {"command": "ves", "description": "записать вес: /ves 82.5"},
            {"command": "help", "description": "что умеет бот"},
        ])
    except Exception:  # noqa: BLE001 — некритично
        pass

    bind_code = security.new_token(16)
    expires = _bind_expiry()
    conn.execute(
        """UPDATE users SET bot_token_enc = ?, bot_username = ?, webhook_secret = ?,
             tg_user_id = NULL, bind_code_hash = ?, bind_code_expires = ?
           WHERE user_id = ?""",
        (security.encrypt_bot_token(token), bot_username, webhook_secret,
         security.token_hash(bind_code), expires, user["user_id"]),
    )
    conn.commit()
    return {"ok": True, "bot_username": bot_username, "bind_code": bind_code,
            "bind_expires": expires,
            "hint": f"отправь боту @{bot_username}: /start {bind_code}"}


def _bind_expiry() -> str:
    from datetime import timedelta
    return (datetime.now(timezone.utc) + timedelta(minutes=BIND_CODE_TTL_MIN)) \
        .strftime("%Y-%m-%dT%H:%M:%SZ")


@app.post("/bot/bind_code")
def bot_bind_code(
    user: dict = Depends(require_user),
    conn: sqlite3.Connection = Depends(get_conn),
):
    """Перевыпуск одноразового кода привязки (истёк / сожжён)."""
    if not user.get("bot_token_enc"):
        raise HTTPException(status_code=409, detail="сначала подключи бота (токен)")
    bind_code = security.new_token(16)
    expires = _bind_expiry()
    conn.execute(
        "UPDATE users SET bind_code_hash = ?, bind_code_expires = ? WHERE user_id = ?",
        (security.token_hash(bind_code), expires, user["user_id"]),
    )
    conn.commit()
    return {"ok": True, "bind_code": bind_code, "bind_expires": expires}


@app.post("/bot/disconnect")
def bot_disconnect(
    user: dict = Depends(require_user),
    conn: sqlite3.Connection = Depends(get_conn),
):
    token = _user_bot_token(user)
    if token:
        try:
            telegram.delete_webhook(token)
        except Exception:  # noqa: BLE001 — отключаем в любом случае
            pass
    conn.execute(
        """UPDATE users SET bot_token_enc = NULL, bot_username = NULL,
             webhook_secret = NULL, tg_user_id = NULL,
             bind_code_hash = NULL, bind_code_expires = NULL
           WHERE user_id = ?""",
        (user["user_id"],),
    )
    conn.commit()
    return {"ok": True}


@app.get("/bot/status")
def bot_status(user: dict = Depends(require_user)):
    token = _user_bot_token(user)
    if not token:
        return {"connected": False}
    try:
        info = telegram.get_webhook_info(token)
    except Exception as e:  # noqa: BLE001
        return {"connected": True, "username": user.get("bot_username"),
                "bound": bool(user.get("tg_user_id")), "webhook_ok": False,
                "error": str(e)}
    expected = f"{_public_url()}/tg/webhook/{user['user_id']}"
    return {
        "connected": True, "username": user.get("bot_username"),
        "bound": bool(user.get("tg_user_id")),
        "webhook_ok": info.get("url") == expected,
        "pending_update_count": info.get("pending_update_count", 0),
        "last_error_message": info.get("last_error_message"),
    }


# ---------------------------------------------------------------------------
# Weight CSV / manual — общий upsert (user_id обязателен)
# ---------------------------------------------------------------------------

def parse_weight_csv(text: str) -> list[dict]:
    """WeightDrop export (date,weight,notes) → строки. ValueError на мусор."""
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    fields = [f.strip().lower() for f in (reader.fieldnames or [])]
    if "date" not in fields or "weight" not in fields:
        raise ValueError("expected WeightDrop CSV with header: date,weight,notes")
    rows = []
    for i, rec in enumerate(reader, start=2):
        rec = {(k or "").strip().lower(): (v or "").strip() for k, v in rec.items()}
        date_s, weight_s = rec.get("date", ""), rec.get("weight", "")
        if not date_s and not weight_s:
            continue
        try:
            datetime.strptime(date_s, "%Y-%m-%d")
            weight = float(weight_s)
        except ValueError:
            raise ValueError(f"line {i}: bad date/weight: {date_s!r}, {weight_s!r}")
        note = rec.get("notes") or None
        rows.append({"measure_date": date_s, "weight_kg": weight, "note": note})
    return rows


def upsert_weight(conn: sqlite3.Connection, user_id: str, rows: list[dict],
                  source: str = "WeightDrop") -> dict:
    """Upsert по (user_id, measure_date), last-write-wins."""
    existing = {
        r["measure_date"]
        for r in conn.execute("SELECT measure_date FROM weight WHERE user_id = ?",
                              (user_id,)).fetchall()
    }
    now = _now()
    inserted = updated = 0
    for row in rows:
        conn.execute(
            """INSERT INTO weight (user_id, measure_date, weight_kg, note, source, ingested_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(user_id, measure_date) DO UPDATE SET
                 weight_kg=excluded.weight_kg, note=excluded.note,
                 source=excluded.source, ingested_at=excluded.ingested_at""",
            (user_id, row["measure_date"], row["weight_kg"], row["note"], source, now),
        )
        if row["measure_date"] in existing:
            updated += 1
        else:
            inserted += 1
            existing.add(row["measure_date"])
    conn.commit()
    return {"accepted": inserted, "updated": updated}


@app.post("/weight/entry")
def weight_entry(
    payload: dict = Body(...),
    user: dict = Depends(require_user),
    conn: sqlite3.Connection = Depends(get_conn),
):
    """Ручной ввод веса с дэша (§5.5 — дэш самодостаточен без бота)."""
    date_s = (payload.get("measure_date") or "").strip()
    try:
        datetime.strptime(date_s, "%Y-%m-%d")
        weight = float(payload.get("weight_kg"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="нужны measure_date (ГГГГ-ММ-ДД) и weight_kg")
    if not (20 <= weight <= 400):
        raise HTTPException(status_code=400, detail="вес вне разумного диапазона 20–400 кг")
    res = upsert_weight(conn, user["user_id"],
                        [{"measure_date": date_s, "weight_kg": weight, "note": None}],
                        source="manual")
    return {"ok": True, **res}


# ---------------------------------------------------------------------------
# Lab upsert (общий для NDJSON-бэкфилла и PDF-confirm) — user_id обязателен
# ---------------------------------------------------------------------------

_LAB_REQUIRED = ("analyte_id", "panel", "sample_date", "seq", "source")

_LAB_UPSERT_SQL = """INSERT INTO lab_results
     (user_id, analyte_id, panel, sample_date, seq, value_num, value_text,
      unit, ref_low, ref_high, ref_raw, source, ingested_at)
   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
   ON CONFLICT(user_id, analyte_id, sample_date, seq) DO UPDATE SET
     panel=excluded.panel, value_num=excluded.value_num,
     value_text=excluded.value_text, unit=excluded.unit,
     ref_low=excluded.ref_low, ref_high=excluded.ref_high,
     ref_raw=excluded.ref_raw, source=excluded.source,
     ingested_at=excluded.ingested_at"""


def _same_reading(prev, rec) -> bool:
    def num_eq(a, b):
        if a is None or b is None:
            return a is None and b is None
        return abs(float(a) - float(b)) < 1e-9
    return (
        num_eq(prev["value_num"], rec.get("value_num"))
        and (prev["value_text"] or None) == (rec.get("value_text") or None)
        and num_eq(prev["ref_low"], rec.get("ref_low"))
        and num_eq(prev["ref_high"], rec.get("ref_high"))
    )


def _upsert_lab_records(conn: sqlite3.Connection, user_id: str,
                        records: list[dict], now: str) -> dict:
    """Upsert canonical rows. Идемпотентно по (user_id, analyte_id, sample_date, seq)."""
    known = {
        r["analyte_id"]
        for r in conn.execute("SELECT analyte_id FROM analyte_meta").fetchall()
    }
    accepted = inserted = changed = unchanged = 0
    rejects = []
    for i, rec in enumerate(records, start=1):
        missing = [f for f in _LAB_REQUIRED if rec.get(f) is None]
        if missing:
            rejects.append({"line": i, "reason": f"missing fields: {', '.join(missing)}"})
            continue
        if rec.get("value_num") is None and rec.get("value_text") is None:
            rejects.append({"line": i, "reason": "both value_num and value_text are null"})
            continue
        if rec["analyte_id"] not in known:
            rejects.append({"line": i, "reason": f"unmapped analyte_id: {rec['analyte_id']}"})
            continue
        prev = conn.execute(
            "SELECT value_num, value_text, ref_low, ref_high FROM lab_results"
            " WHERE user_id = ? AND analyte_id = ? AND sample_date = ? AND seq = ?",
            (user_id, rec["analyte_id"], rec["sample_date"], int(rec["seq"])),
        ).fetchone()
        conn.execute(_LAB_UPSERT_SQL, (
            user_id, rec["analyte_id"], rec["panel"], rec["sample_date"], int(rec["seq"]),
            rec.get("value_num"), rec.get("value_text"), rec.get("unit"),
            rec.get("ref_low"), rec.get("ref_high"), rec.get("ref_raw"),
            rec["source"], now,
        ))
        accepted += 1
        if prev is None:
            inserted += 1
        elif _same_reading(prev, rec):
            unchanged += 1
        else:
            changed += 1
    conn.commit()
    return {
        "accepted": accepted, "inserted": inserted, "changed": changed,
        "unchanged": unchanged, "rejected": len(rejects), "rejects": rejects,
    }


# ---------------------------------------------------------------------------
# PDF ingest: превью → confirm (сессия; квоты §9)
# ---------------------------------------------------------------------------

def _summary_view(preview: dict) -> dict:
    return {
        "ok": preview.get("ok"),
        "reason": preview.get("reason"),
        "dates": preview.get("dates", []),
        "row_count": preview.get("row_count", 0),
        "rejects": preview.get("rejects", []),
        "summary": preview.get("summary", ""),
        "weak": preview.get("weak", False),
    }


def _create_pending(conn, user_id: str, source: str, chat_id, filename: str,
                    preview: dict) -> str:
    pid = uuid.uuid4().hex
    conn.execute(
        """INSERT INTO pending_uploads
             (id, user_id, source, chat_id, filename, created_at, summary_json, rows_json, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending')""",
        (
            pid, user_id, source, (str(chat_id) if chat_id is not None else None),
            filename, _now(),
            json.dumps(_summary_view(preview), ensure_ascii=False),
            json.dumps(preview.get("rows", []), ensure_ascii=False),
        ),
    )
    conn.commit()
    return pid


def _confirm_pending(conn, user_id: str, pid: str) -> dict:
    """Заливает pending в lab_results. Только своя запись (user_id в WHERE)."""
    row = conn.execute(
        "SELECT * FROM pending_uploads WHERE id = ? AND user_id = ?", (pid, user_id)
    ).fetchone()
    if not row:
        return {"status": "not_found"}
    d = dict(row)
    if d["status"] == "committed":
        return {"status": "already_committed", "id": pid}
    if d["status"] == "cancelled":
        return {"status": "cancelled", "id": pid}
    records = json.loads(d["rows_json"])
    result = _upsert_lab_records(conn, user_id, records, _now())
    conn.execute("UPDATE pending_uploads SET status='committed' WHERE id = ?", (pid,))
    conn.commit()
    summary = json.loads(d["summary_json"])
    return {"status": "committed", "id": pid, "dates": summary.get("dates", []), **result}


def _pdf_quota_left(conn, user_id: str) -> int:
    today = _now()[:10]
    used = conn.execute(
        "SELECT COUNT(*) c FROM pending_uploads WHERE user_id = ? AND created_at >= ?",
        (user_id, today),
    ).fetchone()["c"]
    return max(PDF_DAILY_QUOTA - used, 0)


def _plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not (12 <= n % 100 <= 14):
        return few
    return many


def _vals(n: int) -> str:
    return f"{n} {_plural(n, 'значение', 'значения', 'значений')}"


def _commit_note(res: dict) -> str:
    if res.get("status") == "already_committed":
        return "✅ Уже было залито."
    new = res.get("inserted", 0)
    chg = res.get("changed", 0)
    dup = res.get("unchanged", 0)
    dates = ", ".join(res.get("dates", [])) or "—"
    if new == 0 and chg == 0:
        return f"📋 Этот анализ уже есть в системе (все {_vals(dup)} за {dates}) — новых данных нет."
    if new > 0 and chg == 0:
        tail = f" ({dup} уже были)" if dup else ""
        return f"✅ Залил {_vals(new)} за {dates}{tail}. Обнови дэш."
    if new == 0 and chg > 0:
        tail = f", {dup} без изменений" if dup else ""
        return f"♻️ Обновил {_vals(chg)} за {dates} (результаты изменились){tail}. Обнови дэш."
    return f"✅ Залил {new} новых и обновил {_vals(chg)} за {dates}. Обнови дэш."


def _fmt_num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    return str(int(f)) if f == int(f) else str(f)


def _format_preview_values(preview: dict, max_lines: int = 60) -> str:
    rows = preview.get("rows") or []
    dates = preview.get("dates") or []
    head = f"📄 Распознал {_vals(preview.get('row_count', len(rows)))}"
    if len(dates) == 1:
        head += f" за {dates[0]}"
    head += ":"
    by_date = {}
    for r in rows:
        by_date.setdefault(r.get("sample_date"), []).append(r)

    def line(r):
        val = r.get("value_num")
        val = _fmt_num(val) if val is not None else (r.get("value_text") or "—")
        unit = r.get("unit") or ""
        name = r.get("name_ru") or r.get("analyte_id")
        return f"• {name}: {val}{(' ' + unit) if unit else ''}"

    out = [head]
    multi = len(by_date) > 1
    shown = 0
    truncated = False
    for d in (dates or list(by_date.keys())):
        rs = by_date.get(d, [])
        if not rs:
            continue
        if multi:
            out.append(f"\n{d}:")
        for r in rs:
            if shown >= max_lines:
                truncated = True
                break
            out.append(line(r))
            shown += 1
        if truncated:
            break
    if truncated:
        out.append(f"… (+{len(rows) - shown} ещё)")
    rejects = preview.get("rejects") or []
    if rejects:
        rj = sorted({x["name"] for x in rejects})
        out.append(f"\nНе распознал ({len(rj)}): {', '.join(rj[:10])}"
                   + (" …" if len(rj) > 10 else ""))
    return "\n".join(out)


def _yesno_keyboard(pending_id: str) -> dict:
    return {"inline_keyboard": [[
        {"text": "✅ Да", "callback_data": f"pdf_yes:{pending_id}"},
        {"text": "❌ Нет", "callback_data": f"pdf_no:{pending_id}"},
    ]]}


def _latest_pending_id(conn, user_id: str, chat_id) -> Optional[str]:
    """Свежий pending ДЛЯ ЭТОГО ЮЗЕРА и чата (§3.2: user_id обязателен)."""
    r = conn.execute(
        "SELECT id FROM pending_uploads WHERE user_id = ? AND chat_id = ?"
        " AND status = 'pending' ORDER BY created_at DESC LIMIT 1",
        (user_id, str(chat_id)),
    ).fetchone()
    return r["id"] if r else None


def _build_pdf_preview(data: bytes, name: str):
    from lab_ingest import build_preview, PopplerMissing
    try:
        return build_preview(data, name), None
    except PopplerMissing as e:
        return None, str(e)


@app.post("/ingest/pdf")
async def ingest_pdf(
    file: UploadFile = File(...),
    user: dict = Depends(require_user),
    conn: sqlite3.Connection = Depends(get_conn),
):
    """PDF с дэша → парс → PENDING-превью. Авторизация: сессия. Квоты: §9."""
    name = file.filename or "upload.pdf"
    if not name.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="ожидается PDF-файл (.pdf)")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="пустой файл")
    if len(data) > MAX_PDF_BYTES:
        raise HTTPException(status_code=413, detail="файл больше 25 МБ")
    if _pdf_quota_left(conn, user["user_id"]) <= 0:
        raise HTTPException(status_code=429,
                            detail=f"дневная квота {PDF_DAILY_QUOTA} PDF исчерпана, попробуй завтра")
    preview, err = _build_pdf_preview(data, name)
    if err:
        raise HTTPException(status_code=503, detail=err)
    pending_id = None
    if preview.get("ok"):
        pending_id = _create_pending(conn, user["user_id"], "dash", None, name, preview)
    return {"filename": name, "pending_id": pending_id, **preview}


@app.post("/ingest/pdf/confirm")
def ingest_pdf_confirm(
    id: str = Query(...),
    user: dict = Depends(require_user),
    conn: sqlite3.Connection = Depends(get_conn),
):
    res = _confirm_pending(conn, user["user_id"], id)
    if res["status"] == "not_found":
        raise HTTPException(status_code=404, detail="pending-запись не найдена")
    if res["status"] == "cancelled":
        raise HTTPException(status_code=409, detail="эта заливка была отменена")
    if res["status"] == "already_committed":
        return {"ok": True, "already_committed": True, "id": id, "message": _commit_note(res)}
    return {"ok": True, "id": id, "dates": res.get("dates", []),
            "accepted": res["accepted"], "inserted": res["inserted"],
            "changed": res["changed"], "unchanged": res["unchanged"],
            "rejected": res["rejected"], "rejects": res["rejects"],
            "message": _commit_note(res)}


@app.post("/ingest/pdf/cancel")
def ingest_pdf_cancel(
    id: str = Query(...),
    user: dict = Depends(require_user),
    conn: sqlite3.Connection = Depends(get_conn),
):
    row = conn.execute(
        "SELECT status FROM pending_uploads WHERE id = ? AND user_id = ?",
        (id, user["user_id"]),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="pending-запись не найдена")
    if row["status"] == "committed":
        raise HTTPException(status_code=409, detail="уже залито — отмена невозможна")
    conn.execute("UPDATE pending_uploads SET status='cancelled' WHERE id = ?", (id,))
    conn.commit()
    return {"ok": True, "id": id, "status": "cancelled"}


# ---------------------------------------------------------------------------
# Read (сессия; только свои данные)
# ---------------------------------------------------------------------------

@app.get("/labs")
def get_labs(
    panel: Optional[str] = None,
    analyte_id: Optional[str] = None,
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = Query(None, alias="to"),
    user: dict = Depends(require_user),
    conn: sqlite3.Connection = Depends(get_conn),
):
    sql = """SELECT lr.*, am.name_ru, am.direction, am.value_type
             FROM lab_results lr
             LEFT JOIN analyte_meta am USING (analyte_id)
             WHERE lr.user_id = ?"""
    params: list = [user["user_id"]]
    if panel:
        sql += " AND lr.panel = ?"
        params.append(panel)
    if analyte_id:
        sql += " AND lr.analyte_id = ?"
        params.append(analyte_id)
    if from_:
        sql += " AND lr.sample_date >= ?"
        params.append(from_)
    if to:
        sql += " AND lr.sample_date <= ?"
        params.append(to)
    sql += " ORDER BY lr.sample_date, lr.analyte_id, lr.seq"
    out = []
    for r in conn.execute(sql, params).fetchall():
        d = dict(r)
        d.pop("user_id", None)  # своё и так, наружу не светим
        out.append(d)
    return out


@app.get("/weight")
def get_weight(
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = Query(None, alias="to"),
    user: dict = Depends(require_user),
    conn: sqlite3.Connection = Depends(get_conn),
):
    sql = "SELECT measure_date, weight_kg FROM weight WHERE user_id = ?"
    params: list = [user["user_id"]]
    if from_:
        sql += " AND measure_date >= ?"
        params.append(from_)
    if to:
        sql += " AND measure_date <= ?"
        params.append(to)
    sql += " ORDER BY measure_date ASC"
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


@app.get("/dictionary")
def get_dictionary(
    user: dict = Depends(require_user),
    conn: sqlite3.Connection = Depends(get_conn),
):
    rows = conn.execute("SELECT * FROM analyte_meta ORDER BY analyte_id").fetchall()
    return [dict(r) for r in rows]


@app.get("/health")
def health():
    """Публично — только статус. Объёмы и счётчики — /admin/overview (§9)."""
    return {"status": "ok"}


@app.get("/export")
def export(
    user: dict = Depends(require_user),
    conn: sqlite3.Connection = Depends(get_conn),
):
    """Per-user NDJSON-дамп (свои labs + weight) — «Скачать мои данные» (§7)."""
    uid = user["user_id"]
    labs = conn.execute(
        "SELECT * FROM lab_results WHERE user_id = ? ORDER BY sample_date, analyte_id, seq",
        (uid,),
    ).fetchall()
    weights = conn.execute(
        "SELECT * FROM weight WHERE user_id = ? ORDER BY measure_date", (uid,)
    ).fetchall()

    def gen():
        for r in labs:
            d = dict(r)
            d.pop("user_id", None)
            yield json.dumps({"table": "lab_results", **d}, ensure_ascii=False) + "\n"
        for r in weights:
            d = dict(r)
            d.pop("user_id", None)
            yield json.dumps({"table": "weight", **d}, ensure_ascii=False) + "\n"

    fname = f"my_health_data_{_now()[:10]}.ndjson"
    return StreamingResponse(
        gen(), media_type="application/x-ndjson",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


def _report_items(conn, user_id: str, kind: str, id_: str) -> list[dict]:
    col = "analyte_id" if kind == "analyte" else "panel"
    rows = conn.execute(
        f"""SELECT lr.*, am.name_ru, am.direction, am.value_type, am.unit_canonical
            FROM lab_results lr LEFT JOIN analyte_meta am USING (analyte_id)
            WHERE lr.user_id = ? AND lr.{col} = ?
            ORDER BY lr.analyte_id, lr.sample_date, lr.seq""",
        (user_id, id_),
    ).fetchall()
    items: dict[str, dict] = {}
    for r in rows:
        d = dict(r)
        aid = d["analyte_id"]
        it = items.get(aid)
        if it is None:
            it = items[aid] = {
                "name_ru": d.get("name_ru") or aid,
                "unit": d.get("unit") or d.get("unit_canonical") or "",
                "direction": d.get("direction") or "informational",
                "value_type": d.get("value_type") or "quantitative",
                "ref_low": None, "ref_high": None, "points": [],
            }
        if d.get("ref_low") is not None:
            it["ref_low"] = d["ref_low"]
        if d.get("ref_high") is not None:
            it["ref_high"] = d["ref_high"]
        it["points"].append({
            "date": d["sample_date"], "value_num": d.get("value_num"),
            "value_text": d.get("value_text"), "seq": d.get("seq", 0),
            "ref_raw": d.get("ref_raw"),
        })
    return sorted(items.values(), key=lambda x: x["name_ru"])


def _report_filename(kind: str, id_: str, item_list: list[dict]) -> str:
    base = item_list[0]["name_ru"] if kind == "analyte" else id_
    safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in base).strip()[:40]
    return f"{safe or 'report'} {_now()[:10]}.pdf"


def _content_disposition(filename: str) -> str:
    """RFC 5987: кириллические имена файлов не влезают в latin-1 заголовок."""
    from urllib.parse import quote
    ascii_fallback = filename.encode("ascii", "replace").decode("ascii").replace("?", "_")
    return f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{quote(filename)}"


@app.get("/export/report")
def export_report(
    kind: str = Query(..., pattern="^(analyte|panel)$"),
    id: str = Query(...),
    user: dict = Depends(require_user),
    conn: sqlite3.Connection = Depends(get_conn),
):
    """PDF-отчёт скачиванием в браузере (§5.5 — работает без бота)."""
    from export_pdf import build_report_pdf
    item_list = _report_items(conn, user["user_id"], kind, id)
    if not item_list:
        raise HTTPException(status_code=404, detail="нет данных для экспорта")
    pdf_bytes = build_report_pdf(item_list)
    filename = _report_filename(kind, id, item_list)
    return Response(
        content=pdf_bytes, media_type="application/pdf",
        headers={"Content-Disposition": _content_disposition(filename)},
    )


@app.get("/export/telegram")
def export_telegram(
    kind: str = Query(..., pattern="^(analyte|panel)$"),
    id: str = Query(...),
    user: dict = Depends(require_user),
    conn: sqlite3.Connection = Depends(get_conn),
):
    """PDF-отчёт в Telegram — только при подключённом и привязанном боте юзера."""
    from export_pdf import build_report_pdf
    token = _user_bot_token(user)
    if not token or not user.get("tg_user_id"):
        raise HTTPException(status_code=409, detail="бот не подключён — скачай отчёт файлом")
    item_list = _report_items(conn, user["user_id"], kind, id)
    if not item_list:
        raise HTTPException(status_code=404, detail="нет данных для экспорта")
    pdf_bytes = build_report_pdf(item_list)
    filename = _report_filename(kind, id, item_list)
    try:
        telegram.send_document(token, int(user["tg_user_id"]), pdf_bytes, filename,
                               caption=_now()[:10])
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"не удалось отправить в Telegram: {e}")
    return {"ok": True, "sent": True, "filename": filename, "markers": len(item_list)}


# ---------------------------------------------------------------------------
# Админ-канал (LAB_INGEST_TOKEN или сессия role=admin) — §9
# ---------------------------------------------------------------------------

def require_admin_any(
    request: Request,
    authorization: Optional[str] = Header(None),
    session: Optional[str] = Header(None),  # placeholder, real check ниже
):
    """LAB_INGEST_TOKEN (скрипты/cron) ИЛИ админ-сессия (admin-вкладка дэша)."""
    expected = os.environ.get("LAB_INGEST_TOKEN", "")
    if expected and authorization and authorization.startswith("Bearer "):
        import secrets as _secrets
        if _secrets.compare_digest(authorization[len("Bearer "):], expected):
            return {"admin_via": "token"}
    # иначе пробуем сессию с ролью admin
    token = request.cookies.get(SESSION_COOKIE)
    if not token and authorization and authorization.startswith("Bearer "):
        token = authorization[len("Bearer "):]
    if token:
        conn = db.connect()
        try:
            from auth import _session_user
            u = _session_user(conn, token)
        finally:
            conn.close()
        if u and u.get("role") == "admin":
            return {"admin_via": "session", **u}
    raise HTTPException(status_code=401, detail="нужен админ-токен или админ-сессия")


def _require_existing_user(conn, user_id: str) -> dict:
    row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="юзер не найден")
    return dict(row)


@app.post("/admin/invites")
def admin_create_invite(
    payload: dict = Body(default={}),
    admin: dict = Depends(require_admin_any),
    conn: sqlite3.Connection = Depends(get_conn),
):
    from datetime import timedelta
    ttl = int(payload.get("ttl_days") or INVITE_TTL_DAYS_DEFAULT)
    code = security.new_token(24)
    expires = (datetime.now(timezone.utc) + timedelta(days=ttl)).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute(
        "INSERT INTO invites (code, kind, note, created_at, expires_at) VALUES (?, 'invite', ?, ?, ?)",
        (code, payload.get("note"), _now(), expires),
    )
    conn.commit()
    return {"ok": True, "code": code, "expires_at": expires}


@app.post("/admin/reset_code")
def admin_create_reset_code(
    payload: dict = Body(...),
    admin: dict = Depends(require_admin_any),
    conn: sqlite3.Connection = Depends(get_conn),
):
    from datetime import timedelta
    user_id = payload.get("user_id") or ""
    _require_existing_user(conn, user_id)
    code = security.new_token(24)
    expires = (datetime.now(timezone.utc) + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute(
        "INSERT INTO invites (code, kind, for_user, created_at, expires_at)"
        " VALUES (?, 'reset', ?, ?, ?)",
        (code, user_id, _now(), expires),
    )
    conn.commit()
    return {"ok": True, "code": code, "expires_at": expires}


@app.get("/admin/users")
def admin_users(
    admin: dict = Depends(require_admin_any),
    conn: sqlite3.Connection = Depends(get_conn),
):
    """Список юзеров БЕЗ секретов (§10.2: токены наружу не отдаются никогда)."""
    out = []
    for r in conn.execute("SELECT * FROM users ORDER BY created_at").fetchall():
        d = dict(r)
        labs = conn.execute("SELECT COUNT(*) c FROM lab_results WHERE user_id = ?",
                            (d["user_id"],)).fetchone()["c"]
        weight = conn.execute("SELECT COUNT(*) c FROM weight WHERE user_id = ?",
                              (d["user_id"],)).fetchone()["c"]
        out.append({
            "user_id": d["user_id"], "login": d["login"], "name": d["name"],
            "role": d["role"], "status": d["status"], "created_at": d["created_at"],
            "bot_connected": bool(d["bot_token_enc"]), "bot_username": d["bot_username"],
            "bot_bound": bool(d["tg_user_id"]),
            "labs_count": labs, "weight_count": weight,
        })
    return out


@app.get("/admin/overview")
def admin_overview(
    admin: dict = Depends(require_admin_any),
    conn: sqlite3.Connection = Depends(get_conn),
):
    users = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
    labs = conn.execute("SELECT COUNT(*) c FROM lab_results").fetchone()["c"]
    weight = conn.execute("SELECT COUNT(*) c FROM weight").fetchone()["c"]
    analytes = conn.execute("SELECT COUNT(*) c FROM analyte_meta").fetchone()["c"]
    pend = conn.execute(
        "SELECT COUNT(*) c FROM pending_uploads WHERE status='pending'").fetchone()["c"]
    last = conn.execute(
        """SELECT MAX(t) m FROM (
             SELECT MAX(ingested_at) t FROM lab_results
             UNION ALL SELECT MAX(ingested_at) FROM weight)"""
    ).fetchone()["m"]
    return {"users": users, "labs_count": labs, "weight_count": weight,
            "analytes_count": analytes, "pending": pend, "last_ingest": last}


@app.get("/admin/rejects")
def admin_rejects(
    user_id: str = Query(...),
    admin: dict = Depends(require_admin_any),
    conn: sqlite3.Connection = Depends(get_conn),
):
    """Reject-лог по юзеру (M3 плана: цикл «reject → синоним → деплой»)."""
    _require_existing_user(conn, user_id)
    out = []
    for r in conn.execute(
        "SELECT id, filename, created_at, status, summary_json FROM pending_uploads"
        " WHERE user_id = ? ORDER BY created_at DESC LIMIT 100", (user_id,)
    ).fetchall():
        s = json.loads(r["summary_json"])
        if s.get("rejects"):
            out.append({"id": r["id"], "filename": r["filename"],
                        "created_at": r["created_at"], "status": r["status"],
                        "rejects": s["rejects"]})
    return out


@app.post("/admin/reset_labs")
def admin_reset_labs(
    user_id: str = Query(...),   # ОБЯЗАТЕЛЕН: режима «все юзеры» не существует (§9)
    analyte_id: Optional[str] = None,
    admin: dict = Depends(require_admin_any),
    conn: sqlite3.Connection = Depends(get_conn),
):
    _require_existing_user(conn, user_id)
    if analyte_id:
        cur = conn.execute("DELETE FROM lab_results WHERE user_id = ? AND analyte_id = ?",
                           (user_id, analyte_id))
    else:
        cur = conn.execute("DELETE FROM lab_results WHERE user_id = ?", (user_id,))
    conn.commit()
    return {"deleted": cur.rowcount, "user_id": user_id, "scope": analyte_id or "all_labs"}


@app.post("/admin/backfill/labs")
async def admin_backfill_labs(
    request: Request,
    user_id: str = Query(...),
    admin: dict = Depends(require_admin_any),
    conn: sqlite3.Connection = Depends(get_conn),
):
    """NDJSON-бэкфилл конкретному юзеру (разбор инцидентов). user_id обязателен."""
    _require_existing_user(conn, user_id)
    body = (await request.body()).decode("utf-8")
    records = []
    errors = []
    for i, line in enumerate(body.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
            if not isinstance(rec, dict):
                raise ValueError("not a JSON object")
            records.append(rec)
        except ValueError as e:
            errors.append({"line": i, "reason": f"bad JSON: {e}"})
    result = _upsert_lab_records(conn, user_id, records, _now())
    result["rejects"] = errors + result["rejects"]
    result["rejected"] = len(result["rejects"])
    return result


@app.post("/admin/backfill/weight")
async def admin_backfill_weight(
    request: Request,
    user_id: str = Query(...),
    admin: dict = Depends(require_admin_any),
    conn: sqlite3.Connection = Depends(get_conn),
):
    _require_existing_user(conn, user_id)
    text = (await request.body()).decode("utf-8-sig")
    try:
        rows = parse_weight_csv(text)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return upsert_weight(conn, user_id, rows)


@app.delete("/admin/user")
def admin_delete_user(
    user_id: str = Query(...),
    confirm: str = Query(...),
    admin: dict = Depends(require_admin_any),
    conn: sqlite3.Connection = Depends(get_conn),
):
    """Полное вымарывание юзера и его данных (§9: право на удаление)."""
    u = _require_existing_user(conn, user_id)
    if confirm != u["login"]:
        raise HTTPException(status_code=400,
                            detail="confirm должен равняться login удаляемого юзера")
    tok = None
    if u.get("bot_token_enc"):
        try:
            tok = security.decrypt_bot_token(u["bot_token_enc"])
        except Exception:  # noqa: BLE001
            tok = None
    if tok:
        try:
            telegram.delete_webhook(tok)
        except Exception:  # noqa: BLE001
            pass
    counts = {}
    for table in ("lab_results", "weight", "pending_uploads", "sessions"):
        cur = conn.execute(f"DELETE FROM {table} WHERE user_id = ?", (user_id,))  # noqa: S608
        counts[table] = cur.rowcount
    conn.execute("UPDATE invites SET used_by = NULL WHERE used_by = ?", (user_id,))
    conn.execute("DELETE FROM invites WHERE for_user = ?", (user_id,))
    cur = conn.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
    counts["users"] = cur.rowcount
    conn.commit()
    return {"ok": True, "deleted": counts}


@app.post("/ingest/dictionary", dependencies=[Depends(require_admin_token)])
async def ingest_dictionary(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    """Глобальный словарь (YAML/JSON). Full replace analyte_meta. Только токен-канал."""
    body = (await request.body()).decode("utf-8")
    try:
        data: Any = json.loads(body)
    except ValueError:
        try:
            data = yaml.safe_load(body)
        except yaml.YAMLError as e:
            raise HTTPException(status_code=400, detail=f"not valid JSON or YAML: {e}")
    if isinstance(data, dict) and isinstance(data.get("analytes"), list):
        items = data["analytes"]
    elif isinstance(data, dict):
        items = [{"analyte_id": k, **(v or {})} for k, v in data.items()]
    elif isinstance(data, list):
        items = data
    else:
        raise HTTPException(status_code=400, detail="expected a list or mapping of analytes")
    _META_REQUIRED = ("analyte_id", "name_ru", "panel", "value_type", "direction")
    for idx, it in enumerate(items):
        if not isinstance(it, dict):
            raise HTTPException(status_code=400, detail=f"item {idx}: not an object")
        missing = [f for f in _META_REQUIRED if not it.get(f)]
        if missing:
            raise HTTPException(
                status_code=400,
                detail=f"item {idx} ({it.get('analyte_id', '?')}): missing {', '.join(missing)}",
            )
    conn.execute("DELETE FROM analyte_meta")
    conn.executemany(
        """INSERT INTO analyte_meta
             (analyte_id, name_ru, panel, unit_canonical, value_type, direction)
           VALUES (?, ?, ?, ?, ?, ?)""",
        [
            (
                it["analyte_id"], it["name_ru"], it["panel"],
                it.get("unit_canonical"), it["value_type"], it["direction"],
            )
            for it in items
        ],
    )
    conn.commit()
    return {"replaced": len(items)}


@app.post("/admin/auth_bot/setup")
def admin_auth_bot_setup(admin: dict = Depends(require_admin_any)):
    """Ставит вебхук общего бота-вахтёра (AUTH_BOT_TOKEN). Для деплоя."""
    public = _public_url()
    if not public:
        raise HTTPException(status_code=503, detail="PUBLIC_URL не задан")
    try:
        return authbot.setup_webhook(public)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"setWebhook не прошёл: {e}")


@app.post("/tg/auth/webhook")
async def tg_auth_webhook(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    """Вебхук бота-вахтёра (общий бот выдачи инвайтов)."""
    secret = os.environ.get("AUTH_BOT_WEBHOOK_SECRET", "")
    header = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not secret or header != secret:
        raise HTTPException(status_code=403, detail="bad webhook secret")
    update = await request.json()
    authbot.handle_update(conn, update)
    return {"ok": True}


@app.post("/admin/backup")
def admin_backup(
    admin: dict = Depends(require_admin_any),
):
    """Шифрованный бэкап сейчас (для Render Cron). Реализация — backup.py."""
    import backup
    try:
        result = backup.run_backup()
    except backup.BackupNotConfigured as e:
        raise HTTPException(status_code=503, detail=str(e))
    return {"ok": True, **result}


# ---------------------------------------------------------------------------
# Telegram webhook — per-user (§5.1)
# ---------------------------------------------------------------------------

def _webhook_user(conn, user_id: str, header_secret: str) -> Optional[dict]:
    row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    if not d.get("webhook_secret") or not d.get("bot_token_enc"):
        return None
    import secrets as _secrets
    if not _secrets.compare_digest(header_secret or "", d["webhook_secret"]):
        return None
    return d


def _try_bind(conn, user: dict, token: str, chat_id, from_id: str, text: str) -> bool:
    """Обрабатывает /start <код>: одна попытка, TTL. True = сообщение обработано."""
    parts = text.split(maxsplit=1)
    if not parts or parts[0].lower() not in ("/start", "start"):
        return False
    code = parts[1].strip() if len(parts) > 1 else ""
    if user.get("tg_user_id"):
        telegram.send_message(token, chat_id,
                              "Аккаунт уже привязан. Пришли PDF-анализ или /ves 82.5.")
        return True
    if not code:
        telegram.send_message(token, chat_id,
                              "Для привязки открой настройки дэша и пришли: /start <код>")
        return True
    stored_hash = user.get("bind_code_hash")
    expires = user.get("bind_code_expires") or ""
    # одна попытка: код сжигается независимо от результата
    conn.execute("UPDATE users SET bind_code_hash = NULL, bind_code_expires = NULL"
                 " WHERE user_id = ?", (user["user_id"],))
    conn.commit()
    if not stored_hash or expires <= _now():
        telegram.send_message(token, chat_id,
                              "Код истёк. Перевыпусти его в настройках дэша.")
        return True
    if security.token_hash(code) != stored_hash:
        telegram.send_message(token, chat_id,
                              "Код не подошёл (одна попытка). Перевыпусти в настройках дэша.")
        return True
    conn.execute("UPDATE users SET tg_user_id = ? WHERE user_id = ?",
                 (from_id, user["user_id"]))
    conn.commit()
    telegram.send_message(token, chat_id,
                          "✅ Привязал! Теперь присылай PDF-анализы или вес: /ves 82.5")
    return True


def _parse_ves(text: str) -> Optional[tuple[str, float]]:
    """`/ves 82.5 [ГГГГ-ММ-ДД]` (и русское /вес). None если не команда веса."""
    t = text.strip()
    low = t.lower()
    for prefix in ("/ves", "/вес", "вес"):
        if low.startswith(prefix):
            rest = t[len(prefix):].strip()
            if not rest:
                return None
            parts = rest.split()
            try:
                kg = float(parts[0].replace(",", "."))
            except ValueError:
                return None
            date_s = _now()[:10]
            if len(parts) > 1:
                try:
                    datetime.strptime(parts[1], "%Y-%m-%d")
                    date_s = parts[1]
                except ValueError:
                    return None
            return (date_s, kg)
    return None


@app.post("/tg/webhook/{user_id}")
async def tg_webhook(
    user_id: str,
    request: Request,
    conn: sqlite3.Connection = Depends(get_conn),
):
    header = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    user = _webhook_user(conn, user_id, header)
    if not user:
        raise HTTPException(status_code=403, detail="bad webhook secret")
    token = security.decrypt_bot_token(user["bot_token_enc"])
    update = await request.json()

    # --- Инлайн-кнопки Да/Нет ---
    cq = update.get("callback_query")
    if cq:
        from_id = str((cq.get("from") or {}).get("id", ""))
        if not user.get("tg_user_id") or from_id != str(user["tg_user_id"]):
            return {"ok": True}
        data = cq.get("data") or ""
        msg = cq.get("message") or {}
        chat_id = (msg.get("chat") or {}).get("id")
        message_id = msg.get("message_id")
        note = None
        if data.startswith("pdf_yes:"):
            pid = data.split(":", 1)[1]
            res = _confirm_pending(conn, user["user_id"], pid)
            st = res["status"]
            if st in ("committed", "already_committed"):
                note = _commit_note(res)
            elif st == "cancelled":
                note = "Эта заливка была отменена."
            else:
                note = "Не нашёл эту заливку (устарела)."
        elif data.startswith("pdf_no:"):
            pid = data.split(":", 1)[1]
            row = conn.execute(
                "SELECT status FROM pending_uploads WHERE id = ? AND user_id = ?",
                (pid, user["user_id"]),
            ).fetchone()
            if not row:
                note = "Не нашёл эту заливку (устарела)."
            elif row["status"] == "committed":
                note = "Уже залито — отменить нельзя."
            else:
                conn.execute("UPDATE pending_uploads SET status='cancelled' WHERE id = ?", (pid,))
                conn.commit()
                note = "❌ Отменил, ничего не залил."
        try:
            telegram.answer_callback_query(token, cq.get("id", ""))
            if note is not None and chat_id and message_id:
                base = msg.get("text") or ""
                telegram.edit_message_text(token, chat_id, message_id, base + "\n\n" + note)
            elif note:
                telegram.send_message(token, chat_id, note)
        except Exception:  # noqa: BLE001
            if note:
                telegram.send_message(token, chat_id, note)
        return {"ok": True}

    message = update.get("message") or {}
    from_id = str((message.get("from") or {}).get("id", ""))
    chat_id = (message.get("chat") or {}).get("id")
    text_raw = (message.get("text") or "").strip()

    # --- Привязка /start <код> (до привязки — единственное, что принимаем) ---
    if text_raw and _try_bind(conn, user, token, chat_id, from_id, text_raw):
        return {"ok": True}
    if not user.get("tg_user_id") or from_id != str(user["tg_user_id"]):
        return {"ok": True}  # чужим и непривязанным молчим

    doc = message.get("document") or {}
    fname = str(doc.get("file_name", "")).lower()
    text_in = text_raw.lower()

    # --- /вес 82.5 [дата] ---
    ves = _parse_ves(text_raw) if text_raw else None
    if ves:
        date_s, kg = ves
        if not (20 <= kg <= 400):
            telegram.send_message(token, chat_id, "⚠️ Вес вне диапазона 20–400 кг.")
            return {"ok": True}
        upsert_weight(conn, user["user_id"],
                      [{"measure_date": date_s, "weight_kg": kg, "note": None}],
                      source="bot")
        telegram.send_message(token, chat_id, f"✅ Записал {kg:g} кг · {date_s}")
        return {"ok": True}

    # --- CSV веса ---
    if doc.get("file_id") and fname.endswith(".csv"):
        try:
            file_path = telegram.get_file(token, doc["file_id"])
            raw = telegram.download_file(token, file_path)
            rows = parse_weight_csv(raw.decode("utf-8-sig"))
            result = upsert_weight(conn, user["user_id"], rows)
        except ValueError as e:
            telegram.send_message(token, chat_id, f"⚠️ Не смог разобрать CSV: {e}")
            return {"ok": True}
        if rows:
            last = max(rows, key=lambda r: r["measure_date"])
            total = result["accepted"] + result["updated"]
            telegram.send_message(
                token, chat_id,
                f"✅ {total} весов, последний {last['weight_kg']:g} кг · {last['measure_date']}",
            )
        else:
            telegram.send_message(token, chat_id, "⚠️ CSV пустой — ни одной строки веса.")
        return {"ok": True}

    # --- PDF анализов ---
    if doc.get("file_id") and fname.endswith(".pdf"):
        if _pdf_quota_left(conn, user["user_id"]) <= 0:
            telegram.send_message(token, chat_id,
                                  f"⚠️ Дневная квота {PDF_DAILY_QUOTA} PDF исчерпана, попробуй завтра.")
            return {"ok": True}
        try:
            file_path = telegram.get_file(token, doc["file_id"])
            raw = telegram.download_file(token, file_path)
        except Exception as e:  # noqa: BLE001
            telegram.send_message(token, chat_id, f"⚠️ Не смог скачать файл: {e}")
            return {"ok": True}
        if len(raw) > MAX_PDF_BYTES:
            telegram.send_message(token, chat_id, "⚠️ Файл больше 25 МБ.")
            return {"ok": True}
        preview, err = _build_pdf_preview(raw, doc.get("file_name") or "upload.pdf")
        if err:
            telegram.send_message(token, chat_id, "⚠️ Разбор PDF на сервере пока недоступен.")
            return {"ok": True}
        if not preview.get("ok"):
            telegram.send_message(token, chat_id,
                                  "🚫 " + preview.get("summary", "Не смог разобрать."))
            return {"ok": True}
        pid = _create_pending(conn, user["user_id"], "telegram", chat_id,
                              doc.get("file_name") or "upload.pdf", preview)
        telegram.send_message(
            token, chat_id,
            _format_preview_values(preview) + "\n\nЗалить?",
            reply_markup=_yesno_keyboard(pid),
        )
        return {"ok": True}

    # --- Текстовые Да/Нет (запасной путь) ---
    if text_in in ("да", "yes", "y", "ок", "ok", "+"):
        pid = _latest_pending_id(conn, user["user_id"], chat_id)
        if not pid:
            telegram.send_message(token, chat_id, "Нечего подтверждать — пришли PDF-анализ.")
            return {"ok": True}
        res = _confirm_pending(conn, user["user_id"], pid)
        if res["status"] in ("committed", "already_committed"):
            telegram.send_message(token, chat_id, _commit_note(res))
        else:
            telegram.send_message(token, chat_id, "Готово (уже было залито).")
        return {"ok": True}
    if text_in in ("нет", "no", "n", "-", "отмена"):
        pid = _latest_pending_id(conn, user["user_id"], chat_id)
        if pid:
            conn.execute("UPDATE pending_uploads SET status='cancelled' WHERE id = ?", (pid,))
            conn.commit()
            telegram.send_message(token, chat_id, "❌ Отменил, ничего не залил.")
        else:
            telegram.send_message(token, chat_id, "Нечего отменять.")
        return {"ok": True}

    if text_in in ("/help", "help"):
        telegram.send_message(
            token, chat_id,
            "Пришли PDF-анализ с текстовым слоем — залью в дэш.\n"
            "Вес: /ves 82.5 (можно с датой: /ves 82.5 2026-07-01) или CSV из WeightDrop.",
        )
        return {"ok": True}

    telegram.send_message(
        token, chat_id,
        "Пришли PDF-анализ, CSV веса или /ves 82.5. Помощь: /help",
    )
    return {"ok": True}
