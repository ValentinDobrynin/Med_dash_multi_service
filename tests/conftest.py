# -*- coding: utf-8 -*-
"""Мультитенантные фикстуры (PLAN_multiuser v3).

Особенности:
- Сессии в тестах ходят Bearer-токеном (require_user принимает его наравне с cookie),
  потому что у TestClient один cookie-jar на всех «юзеров».
- Telegram полностью фейковый: фикстура tg перехватывает вызовы и запоминает,
  ЧЕРЕЗ КАКОЙ ТОКЕН ушло каждое сообщение (тест «два бота крест-накрест»).
- PDF-парсер подменяется canned-превью (мультитенантная логика не зависит от poppler).
"""
import sys
from pathlib import Path

import pytest

SERVICE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVICE_DIR))

ADMIN_TOKEN = "test-admin-token"
ADMIN_HEADERS = {"Authorization": f"Bearer {ADMIN_TOKEN}"}


def canned_preview(marker_value: float = 5.4) -> dict:
    rows = [
        {"analyte_id": "ldl_c", "panel": "lipids_cardio", "sample_date": "2026-01-15",
         "seq": 0, "value_num": marker_value, "value_text": None, "unit": "ммоль/л",
         "ref_low": 0, "ref_high": 3.0, "ref_raw": "0 - 3,0", "source": "TEST",
         "name_ru": "ЛПНП"},
        {"analyte_id": "hemoglobin", "panel": "blood_count", "sample_date": "2026-01-15",
         "seq": 0, "value_num": 150.0, "value_text": None, "unit": "г/л",
         "ref_low": 130, "ref_high": 170, "ref_raw": "130-170", "source": "TEST",
         "name_ru": "Гемоглобин"},
    ]
    return {"ok": True, "reason": None, "dates": ["2026-01-15"], "row_count": len(rows),
            "rows": rows, "rejects": [], "summary": "", "weak": False,
            "used_fallback": False}


@pytest.fixture
def client(tmp_path, monkeypatch):
    from cryptography.fernet import Fernet
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("LAB_INGEST_TOKEN", ADMIN_TOKEN)
    monkeypatch.setenv("BOT_TOKEN_FERNET_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("COOKIE_SECURE", "0")
    monkeypatch.setenv("PUBLIC_URL", "https://svc.test")
    monkeypatch.delenv("ADMIN_LOGIN", raising=False)
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    monkeypatch.delenv("BACKUP_PUBLIC_KEY_PEM", raising=False)

    import security
    security._reset_rate_limit_for_tests()

    from fastapi.testclient import TestClient
    import app as appmod
    with TestClient(appmod.app) as c:
        yield c


@pytest.fixture
def tg(monkeypatch):
    """Фейковый telegram: перехват всех вызовов с фиксацией токена."""
    import app as appmod
    tgmod = appmod.telegram
    calls = {"sent": [], "docs": [], "webhooks": [], "deleted": [],
             "edited": [], "answered": [], "commands": []}
    monkeypatch.setattr(tgmod, "get_me",
                        lambda token: {"id": 1, "username": f"bot_{token.split(':', 1)[0]}"})
    monkeypatch.setattr(tgmod, "set_webhook",
                        lambda token, url, secret_token: calls["webhooks"].append(
                            {"token": token, "url": url, "secret": secret_token}))
    monkeypatch.setattr(tgmod, "delete_webhook", lambda token: calls["deleted"].append(token))
    monkeypatch.setattr(tgmod, "get_webhook_info",
                        lambda token: {"url": next(
                            (w["url"] for w in reversed(calls["webhooks"]) if w["token"] == token), ""),
                            "pending_update_count": 0})
    monkeypatch.setattr(tgmod, "send_message",
                        lambda token, chat_id, text, reply_markup=None: calls["sent"].append(
                            {"token": token, "chat_id": chat_id, "text": text,
                             "reply_markup": reply_markup}))
    monkeypatch.setattr(tgmod, "send_document",
                        lambda token, chat_id, content, filename, caption="",
                               mime="application/pdf": calls["docs"].append(
                            {"token": token, "chat_id": chat_id, "filename": filename,
                             "content": content}))
    monkeypatch.setattr(tgmod, "edit_message_text",
                        lambda token, chat_id, message_id, text, reply_markup=None:
                        calls["edited"].append({"token": token, "chat_id": chat_id,
                                                "message_id": message_id, "text": text}))
    monkeypatch.setattr(tgmod, "answer_callback_query",
                        lambda token, cqid, text="": calls["answered"].append(
                            {"token": token, "id": cqid}))
    monkeypatch.setattr(tgmod, "set_my_commands",
                        lambda token, commands: calls["commands"].append(token))
    return calls


@pytest.fixture
def canned_pdf(monkeypatch):
    """Подменяет разбор PDF canned-превью; значение ЛПНП настраивается per-call."""
    import app as appmod
    state = {"value": 5.4}
    monkeypatch.setattr(appmod, "_build_pdf_preview",
                        lambda data, name: (canned_preview(state["value"]), None))
    return state


@pytest.fixture
def make_user(client):
    """Создаёт юзера через инвайт. Возвращает dict с headers (Bearer-сессия)."""
    def _make(login: str, password: str = "secret-123", name: str | None = None):
        r = client.post("/admin/invites", headers=ADMIN_HEADERS, json={"note": login})
        assert r.status_code == 200, r.text
        code = r.json()["code"]
        r = client.post("/auth/register", json={
            "code": code, "login": login, "name": name or login.title(),
            "password": password, "consent": True,
        })
        assert r.status_code == 200, r.text
        token = r.cookies.get("session") or client.cookies.get("session")
        assert token, "session cookie not set"
        client.cookies.clear()
        return {
            "user_id": r.json()["user_id"], "login": login, "password": password,
            "headers": {"Authorization": f"Bearer {token}"},
        }
    return _make


def connect_bot(client, tg, user: dict, bot_token: str, from_id: str):
    """Полный флоу подключения бота: connect → bind /start <код>. Возвращает
    контекст для последующих вебхук-запросов."""
    r = client.post("/bot/connect", json={"token": bot_token}, headers=user["headers"])
    assert r.status_code == 200, r.text
    bind_code = r.json()["bind_code"]
    hook = tg["webhooks"][-1]
    assert hook["token"] == bot_token
    path = hook["url"].replace("https://svc.test", "")
    r2 = client.post(path, headers={"X-Telegram-Bot-Api-Secret-Token": hook["secret"]},
                     json={"message": {"from": {"id": int(from_id)},
                                       "chat": {"id": int(from_id)},
                                       "text": f"/start {bind_code}"}})
    assert r2.status_code == 200, r2.text
    assert any("Привязал" in s["text"] for s in tg["sent"] if s["token"] == bot_token)
    return {"secret": hook["secret"], "webhook_path": path, "from_id": from_id,
            "bot_token": bot_token}


def hook_message(client, bot: dict, **message):
    """Сообщение в вебхук от имени привязанного юзера бота."""
    payload = {"message": {"from": {"id": int(bot["from_id"])},
                           "chat": {"id": int(bot["from_id"])}, **message}}
    return client.post(bot["webhook_path"],
                       headers={"X-Telegram-Bot-Api-Secret-Token": bot["secret"]},
                       json=payload)


def hook_callback(client, bot: dict, data: str, message_id: int = 77):
    payload = {"callback_query": {"id": "cq1", "from": {"id": int(bot["from_id"])},
                                  "data": data,
                                  "message": {"message_id": message_id,
                                              "chat": {"id": int(bot["from_id"])},
                                              "text": "preview"}}}
    return client.post(bot["webhook_path"],
                       headers={"X-Telegram-Bot-Api-Secret-Token": bot["secret"]},
                       json=payload)
