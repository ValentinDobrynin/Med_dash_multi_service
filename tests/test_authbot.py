# -*- coding: utf-8 -*-
"""Бот-вахтёр (authbot): запрос доступа → одобрение админом → инвайт-ссылка.

Telegram фейковый (фикстура tg). Проверяем полный флоу, отказ, антифлуд,
что чужой не может одобрять, и что выданный код реально работает в /auth/register.
"""
ADMIN_TG = "500"
USER_TG = "900"
WEBHOOK = "/tg/auth/webhook"
SECRET = "auth-secret"
AUTH_BOT = "77:AUTH-TOKEN"


def _env(monkeypatch):
    monkeypatch.setenv("AUTH_BOT_TOKEN", AUTH_BOT)
    monkeypatch.setenv("AUTH_BOT_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("AUTH_ADMIN_TG_IDS", ADMIN_TG)
    monkeypatch.setenv("DASH_URL", "https://dash.test")


def _msg(client, tg_id, text, username=None):
    return client.post(WEBHOOK, headers={"X-Telegram-Bot-Api-Secret-Token": SECRET},
                       json={"message": {"from": {"id": int(tg_id), "username": username},
                                         "chat": {"id": int(tg_id)}, "text": text}})


def _cb(client, tg_id, data, message_id=10):
    return client.post(WEBHOOK, headers={"X-Telegram-Bot-Api-Secret-Token": SECRET},
                       json={"callback_query": {"id": "c1", "from": {"id": int(tg_id)},
                                                "data": data,
                                                "message": {"message_id": message_id,
                                                            "chat": {"id": int(tg_id)},
                                                            "text": "req"}}})


def _last_kb_req_id(tg):
    """id заявки из последнего сообщения админу с кнопками."""
    for s in reversed(tg["sent"]):
        if s["chat_id"] == int(ADMIN_TG) and s["reply_markup"]:
            return s["reply_markup"]["inline_keyboard"][0][0]["callback_data"].split(":", 1)[1]
    return None


def test_bad_secret_403(client, tg, monkeypatch):
    _env(monkeypatch)
    r = client.post(WEBHOOK, headers={"X-Telegram-Bot-Api-Secret-Token": "nope"}, json={})
    assert r.status_code == 403


def test_dash_url_falls_back_to_cors(client, tg, monkeypatch):
    """DASH_URL не задан → берём первый origin из CORS_ORIGINS."""
    monkeypatch.setenv("AUTH_BOT_TOKEN", AUTH_BOT)
    monkeypatch.setenv("AUTH_BOT_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("AUTH_ADMIN_TG_IDS", ADMIN_TG)
    monkeypatch.delenv("DASH_URL", raising=False)
    monkeypatch.setenv("CORS_ORIGINS", "https://cors-dash.test, https://other.test")
    _msg(client, USER_TG, "/start")
    _msg(client, USER_TG, "Кор Корс")
    req_id = _last_kb_req_id(tg)
    _cb(client, ADMIN_TG, f"acc_ok:{req_id}")
    assert any("cors-dash.test/?code=" in s["text"]
               for s in tg["sent"] if s["chat_id"] == int(USER_TG))


def test_full_approve_flow_issues_working_invite(client, tg, monkeypatch):
    _env(monkeypatch)
    # 1) /start → бот просит имя
    _msg(client, USER_TG, "/start", username="petya")
    assert any(s["chat_id"] == int(USER_TG) and "имя" in s["text"].lower() for s in tg["sent"])
    # 2) имя → админу уходит запрос с кнопками
    _msg(client, USER_TG, "Пётр Петров", username="petya")
    admin_msgs = [s for s in tg["sent"] if s["chat_id"] == int(ADMIN_TG) and s["reply_markup"]]
    assert admin_msgs and "Пётр Петров" in admin_msgs[-1]["text"]
    assert admin_msgs[-1]["token"] == AUTH_BOT
    # заявителю — «отправил администратору»
    assert any(s["chat_id"] == int(USER_TG) and "администратор" in s["text"].lower() for s in tg["sent"])

    # 3) админ одобряет → заявителю уходит ссылка с кодом
    req_id = _last_kb_req_id(tg)
    _cb(client, ADMIN_TG, f"acc_ok:{req_id}")
    link_msgs = [s for s in tg["sent"] if s["chat_id"] == int(USER_TG) and "dash.test/?code=" in s["text"]]
    assert link_msgs, "ссылка заявителю не отправлена"
    code = link_msgs[-1]["text"].split("code=")[1].split()[0].strip()

    # 4) код реально работает в регистрации
    r = client.post("/auth/register", json={
        "code": code, "login": "petya", "name": "Пётр", "password": "password-123", "consent": True})
    assert r.status_code == 200, r.text


def test_reject_flow_and_cooldown(client, tg, monkeypatch):
    _env(monkeypatch)
    _msg(client, USER_TG, "/start")
    _msg(client, USER_TG, "Иван Иванов")
    req_id = _last_kb_req_id(tg)
    _cb(client, ADMIN_TG, f"acc_no:{req_id}")
    assert any(s["chat_id"] == int(USER_TG) and "не одобрен" in s["text"].lower() for s in tg["sent"])
    # повтор сразу — антифлуд
    n = len(tg["sent"])
    _msg(client, USER_TG, "/start")
    assert any("через" in s["text"].lower() for s in tg["sent"][n:])
    # админу второй запрос не ушёл
    assert not [s for s in tg["sent"][n:] if s["chat_id"] == int(ADMIN_TG)]


def test_stranger_cannot_approve(client, tg, monkeypatch):
    _env(monkeypatch)
    _msg(client, USER_TG, "/start")
    _msg(client, USER_TG, "Гость Гостев")
    req_id = _last_kb_req_id(tg)
    # чужой (не из AUTH_ADMIN_TG_IDS) жмёт одобрить
    _cb(client, "99999", f"acc_ok:{req_id}")
    # заявителю ссылка НЕ ушла
    assert not [s for s in tg["sent"] if s["chat_id"] == int(USER_TG) and "code=" in s["text"]]
    # заявка всё ещё pending — настоящий админ может одобрить
    _cb(client, ADMIN_TG, f"acc_ok:{req_id}")
    assert [s for s in tg["sent"] if s["chat_id"] == int(USER_TG) and "code=" in s["text"]]


def test_double_pending_not_spammed(client, tg, monkeypatch):
    _env(monkeypatch)
    _msg(client, USER_TG, "/start")
    _msg(client, USER_TG, "Анна Аннова")
    n = len(tg["sent"])
    _msg(client, USER_TG, "/start")  # ещё раз, пока pending
    assert any("на рассмотрении" in s["text"].lower() for s in tg["sent"][n:])
    # админу дубль не ушёл
    assert not [s for s in tg["sent"][n:] if s["chat_id"] == int(ADMIN_TG)]


def test_admin_writing_bot_gets_hint(client, tg, monkeypatch):
    _env(monkeypatch)
    _msg(client, ADMIN_TG, "/start")
    assert any(s["chat_id"] == int(ADMIN_TG) and "администратор" in s["text"].lower() for s in tg["sent"])
    # заявка от админа не создаётся
    assert not [s for s in tg["sent"] if s["reply_markup"]]
