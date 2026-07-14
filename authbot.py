"""Бот-вахтёр: единый общий бот для выдачи инвайтов (PLAN_multiuser, доработка).

Флоу:
  1. Новый человек пишет боту → бот просит «Имя Фамилия».
  2. Человек присылает имя → заявка (pending) → всем админам из AUTH_ADMIN_TG_IDS
     прилетает уведомление с кнопками ✅ Одобрить / ❌ Отклонить.
  3. Админ одобряет → бот создаёт инвайт и сам шлёт ссылку заявителю в личку.
     Отклоняет → бот вежливо сообщает заявителю.

Отдельный общий бот (НЕ per-user дата-боты, НЕ личный бот админа): свой токен
AUTH_BOT_TOKEN, свой вебхук /tg/auth/webhook с секретом AUTH_BOT_WEBHOOK_SECRET.
Смена пароля через этого бота — отдельный следующий шаг (не в этой версии).
"""
import os
import uuid
from datetime import datetime, timedelta, timezone

import telegram
import security

INVITE_TTL_DAYS = 7
COOLDOWN_MIN = 30          # после отказа — не спамить админа раньше, чем через полчаса
NAME_MAX = 80


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def token() -> str:
    return os.environ.get("AUTH_BOT_TOKEN", "")


def configured() -> bool:
    return bool(token() and os.environ.get("AUTH_BOT_WEBHOOK_SECRET"))


def admins() -> list[str]:
    return [s.strip() for s in os.environ.get("AUTH_ADMIN_TG_IDS", "").split(",") if s.strip()]


def _dash_url() -> str:
    """Адрес дэша для инвайт-ссылок. DASH_URL, иначе первый origin из CORS_ORIGINS."""
    url = os.environ.get("DASH_URL", "").strip()
    if not url:
        url = next((o.strip() for o in os.environ.get("CORS_ORIGINS", "").split(",")
                    if o.strip()), "")
    return url.rstrip("/")


def _invite_link(code: str) -> str:
    base = _dash_url()
    return f"{base}/?code={code}" if base else f"код регистрации: {code}"


def _kb(req_id: str) -> dict:
    return {"inline_keyboard": [[
        {"text": "✅ Одобрить", "callback_data": f"acc_ok:{req_id}"},
        {"text": "❌ Отклонить", "callback_data": f"acc_no:{req_id}"},
    ]]}


def _latest(conn, tg_id: str):
    return conn.execute(
        "SELECT * FROM access_requests WHERE tg_id = ? ORDER BY created_at DESC LIMIT 1",
        (tg_id,),
    ).fetchone()


def _notify_admins(conn, req_id: str, name: str, username: str | None):
    tok = token()
    uname = f" (@{username})" if username else ""
    text = (f"🔑 Запрос доступа\n\nИмя: {name}{uname}\n\n"
            f"Одобрить — вышлю человеку инвайт-ссылку.")
    for admin_id in admins():
        try:
            telegram.send_message(tok, int(admin_id), text, reply_markup=_kb(req_id))
        except Exception:  # noqa: BLE001 — один недоступный админ не ломает поток
            pass


def _issue_invite(conn, note: str) -> str:
    code = security.new_token(24)
    expires = (datetime.now(timezone.utc) + timedelta(days=INVITE_TTL_DAYS)) \
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute(
        "INSERT INTO invites (code, kind, note, created_at, expires_at)"
        " VALUES (?, 'invite', ?, ?, ?)",
        (code, note, _now(), expires),
    )
    conn.commit()
    return code


def _handle_callback(conn, cq: dict):
    tok = token()
    from_id = str((cq.get("from") or {}).get("id", ""))
    data = cq.get("data") or ""
    msg = cq.get("message") or {}
    chat_id = (msg.get("chat") or {}).get("id")
    message_id = msg.get("message_id")

    def ack(note=None):
        try:
            telegram.answer_callback_query(tok, cq.get("id", ""))
            if note and chat_id and message_id:
                telegram.edit_message_text(tok, chat_id, message_id, note)
        except Exception:  # noqa: BLE001
            pass

    if from_id not in admins():
        try:
            telegram.answer_callback_query(tok, cq.get("id", ""), text="Не для тебя.")
        except Exception:  # noqa: BLE001
            pass
        return

    if ":" not in data:
        ack()
        return
    action, req_id = data.split(":", 1)
    row = conn.execute("SELECT * FROM access_requests WHERE id = ?", (req_id,)).fetchone()
    if not row:
        ack("Заявка не найдена (устарела).")
        return
    r = dict(row)
    if r["status"] != "pending":
        ack(f"Уже обработано ({r['status']}).")
        return

    if action == "acc_ok":
        code = _issue_invite(conn, note=f"authbot: {r['name']}")
        conn.execute(
            "UPDATE access_requests SET status='approved', invite_code=?, decided_at=?, decided_by=?"
            " WHERE id=?", (code, _now(), from_id, req_id))
        conn.commit()
        try:
            telegram.send_message(
                tok, int(r["tg_id"]),
                f"✅ Доступ одобрен!\n\nРегистрация по ссылке (одноразовая, {INVITE_TTL_DAYS} дн.):\n"
                f"{_invite_link(code)}")
        except Exception:  # noqa: BLE001
            pass
        ack(f"✅ Одобрено ({r['name']}) — ссылка отправлена.")
    elif action == "acc_no":
        conn.execute(
            "UPDATE access_requests SET status='rejected', decided_at=?, decided_by=? WHERE id=?",
            (_now(), from_id, req_id))
        conn.commit()
        try:
            telegram.send_message(tok, int(r["tg_id"]),
                                  "К сожалению, доступ не одобрен.")
        except Exception:  # noqa: BLE001
            pass
        ack(f"❌ Отклонено ({r['name']}).")
    else:
        ack()


def _cooldown_active(conn, tg_id: str) -> bool:
    """Недавний отказ — не даём сразу слать новую заявку (антифлуд админу)."""
    row = conn.execute(
        "SELECT status, created_at FROM access_requests WHERE tg_id=? AND status='rejected'"
        " ORDER BY created_at DESC LIMIT 1", (tg_id,)).fetchone()
    if not row:
        return False
    since = datetime.now(timezone.utc) - timedelta(minutes=COOLDOWN_MIN)
    return row["created_at"] >= since.strftime("%Y-%m-%dT%H:%M:%SZ")


def _handle_message(conn, message: dict):
    tok = token()
    frm = message.get("from") or {}
    tg_id = str(frm.get("id", ""))
    chat_id = (message.get("chat") or {}).get("id")
    username = frm.get("username")
    text = (message.get("text") or "").strip()
    if not tg_id or chat_id is None:
        return

    if tg_id in admins():
        telegram.send_message(tok, chat_id, "Ты администратор этого бота 🙂 Запросы приходят кнопками.")
        return

    row = _latest(conn, tg_id)
    st = row["status"] if row else None

    is_start = text.lower() in ("/start", "start", "/help", "help") or not text

    # Уже ждёт решения — не плодим заявки
    if st == "pending":
        telegram.send_message(tok, chat_id, "Заявка уже на рассмотрении — пришлю ссылку, как одобрят.")
        return
    if st == "approved" and row["invite_code"]:
        telegram.send_message(
            tok, chat_id,
            "Тебе уже отправлена инвайт-ссылка. Если не пришла — открой её из прошлого сообщения "
            "или напиши администратору.")
        return

    if is_start or st is None or st == "rejected":
        if _cooldown_active(conn, tg_id):
            telegram.send_message(tok, chat_id,
                                  f"Заявка недавно отклонена. Попробуй снова через {COOLDOWN_MIN} минут.")
            return
        # начинаем новую: ждём имя
        rid = uuid.uuid4().hex
        conn.execute(
            "INSERT INTO access_requests (id, tg_id, tg_username, status, created_at)"
            " VALUES (?, ?, ?, 'awaiting_name', ?)", (rid, tg_id, username, _now()))
        conn.commit()
        telegram.send_message(
            tok, chat_id,
            "Привет! Это доступ к личному кабинету здоровья.\n\n"
            "Напиши своё имя и фамилию — передам администратору на подтверждение.")
        return

    if st == "awaiting_name":
        name = text.strip()
        if not name or len(name) > NAME_MAX:
            telegram.send_message(tok, chat_id, "Напиши имя и фамилию одним сообщением.")
            return
        conn.execute(
            "UPDATE access_requests SET name=?, tg_username=?, status='pending' WHERE id=?",
            (name, username, row["id"]))
        conn.commit()
        _notify_admins(conn, row["id"], name, username)
        telegram.send_message(
            tok, chat_id,
            f"Спасибо, {name}! Отправил запрос администратору. Пришлю ссылку, как только одобрят.")
        return


def handle_update(conn, update: dict):
    """Точка входа вебхука бота-вахтёра."""
    if update.get("callback_query"):
        _handle_callback(conn, update["callback_query"])
    elif update.get("message"):
        _handle_message(conn, update["message"])


def setup_webhook(public_url: str) -> dict:
    """Ставит вебхук общего бота на /tg/auth/webhook + меню команд. Для деплоя."""
    tok = token()
    secret = os.environ.get("AUTH_BOT_WEBHOOK_SECRET", "")
    if not tok or not secret:
        raise RuntimeError("AUTH_BOT_TOKEN / AUTH_BOT_WEBHOOK_SECRET не заданы")
    me = telegram.get_me(tok)
    telegram.set_webhook(tok, f"{public_url.rstrip('/')}/tg/auth/webhook", secret)
    try:
        telegram.set_my_commands(tok, [{"command": "start", "description": "запросить доступ"}])
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True, "bot_username": me.get("username")}
