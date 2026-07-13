# -*- coding: utf-8 -*-
"""M8–M11: подключение бота, bind-флоу, per-user вебхуки, «два бота крест-накрест»,
«два pending — оба шлют Да», квоты."""
import app as appmod
from conftest import connect_bot, hook_callback, hook_message

BOT_A, BOT_B = "111:AAA-token", "222:BBB-token"
TG_A, TG_B = "70001", "70002"


def test_connect_sets_per_user_webhook(client, make_user, tg):
    u = make_user("alice")
    r = client.post("/bot/connect", json={"token": BOT_A}, headers=u["headers"])
    assert r.status_code == 200
    hook = tg["webhooks"][-1]
    assert hook["url"] == f"https://svc.test/tg/webhook/{u['user_id']}"
    assert len(hook["secret"]) >= 20
    assert r.json()["bot_username"] == "bot_111"


def test_bind_wrong_code_burns_once(client, make_user, tg):
    u = make_user("alice")
    r = client.post("/bot/connect", json={"token": BOT_A}, headers=u["headers"])
    good_code = r.json()["bind_code"]
    hook = tg["webhooks"][-1]
    path = hook["url"].replace("https://svc.test", "")

    def start(code):
        return client.post(path, headers={"X-Telegram-Bot-Api-Secret-Token": hook["secret"]},
                           json={"message": {"from": {"id": int(TG_A)},
                                             "chat": {"id": int(TG_A)},
                                             "text": f"/start {code}"}})

    start("wrong-code")
    assert any("не подошёл" in s["text"] for s in tg["sent"])
    # одна попытка: даже правильный код теперь сожжён
    start(good_code)
    assert any("истёк" in s["text"].lower() or "Перевыпусти" in s["text"]
               for s in tg["sent"][-1:])
    # перевыпуск из настроек
    r = client.post("/bot/bind_code", headers=u["headers"])
    start(r.json()["bind_code"])
    assert any("Привязал" in s["text"] for s in tg["sent"])
    me = client.get("/auth/me", headers=u["headers"]).json()
    assert me["bot"]["bound"] is True


def test_webhook_bad_secret_403(client, make_user, tg):
    u = make_user("alice")
    client.post("/bot/connect", json={"token": BOT_A}, headers=u["headers"])
    path = tg["webhooks"][-1]["url"].replace("https://svc.test", "")
    r = client.post(path, headers={"X-Telegram-Bot-Api-Secret-Token": "evil"},
                    json={"message": {"text": "hi"}})
    assert r.status_code == 403
    r = client.post("/tg/webhook/nonexistent-user",
                    headers={"X-Telegram-Bot-Api-Secret-Token": "x"}, json={})
    assert r.status_code == 403


def test_stranger_is_ignored_after_bind(client, make_user, tg):
    u = make_user("alice")
    bot = connect_bot(client, tg, u, BOT_A, TG_A)
    n = len(tg["sent"])
    r = client.post(bot["webhook_path"],
                    headers={"X-Telegram-Bot-Api-Secret-Token": bot["secret"]},
                    json={"message": {"from": {"id": 999999}, "chat": {"id": 999999},
                                      "text": "/ves 80"}})
    assert r.status_code == 200
    assert len(tg["sent"]) == n  # чужому молчим
    assert client.get("/weight", headers=u["headers"]).json() == []


def test_ves_and_cross_bot_isolation(client, make_user, tg):
    """M10: «два бота крест-накрест» — исходящие строго через свой токен, данные свои."""
    a, b = make_user("alice"), make_user("bob")
    bot_a = connect_bot(client, tg, a, BOT_A, TG_A)
    bot_b = connect_bot(client, tg, b, BOT_B, TG_B)

    hook_message(client, bot_a, text="/ves 61.5")
    hook_message(client, bot_b, text="/ves 88.8 2026-06-01")

    wa = client.get("/weight", headers=a["headers"]).json()
    wb = client.get("/weight", headers=b["headers"]).json()
    assert [w["weight_kg"] for w in wa] == [61.5]
    assert [w["weight_kg"] for w in wb] == [88.8]
    assert wb[0]["measure_date"] == "2026-06-01"

    # каждое исходящее сообщение ушло строго через токен своего бота
    for s in tg["sent"]:
        if s["chat_id"] == int(TG_A):
            assert s["token"] == BOT_A
        if s["chat_id"] == int(TG_B):
            assert s["token"] == BOT_B


def test_pdf_flow_via_bot_and_two_pending_yes(client, make_user, tg, canned_pdf,
                                              monkeypatch):
    """PDF через бота → превью+кнопки; «у обоих pending, оба шлют Да» — без пересечений."""
    monkeypatch.setattr(appmod.telegram, "get_file", lambda token, fid: "p/x.pdf")
    monkeypatch.setattr(appmod.telegram, "download_file", lambda token, p: b"%PDF-fake")

    a, b = make_user("alice"), make_user("bob")
    bot_a = connect_bot(client, tg, a, BOT_A, TG_A)
    bot_b = connect_bot(client, tg, b, BOT_B, TG_B)

    canned_pdf["value"] = 5.41
    hook_message(client, bot_a, document={"file_id": "f1", "file_name": "a.pdf"})
    canned_pdf["value"] = 4.99
    hook_message(client, bot_b, document={"file_id": "f2", "file_name": "b.pdf"})

    # у обоих превью с кнопками
    kb_a = [s for s in tg["sent"] if s["token"] == BOT_A and s["reply_markup"]]
    kb_b = [s for s in tg["sent"] if s["token"] == BOT_B and s["reply_markup"]]
    assert kb_a and kb_b

    # оба шлют текстовое «Да» одновременно
    hook_message(client, bot_a, text="Да")
    hook_message(client, bot_b, text="да")

    la = client.get("/labs", headers=a["headers"]).text
    lb = client.get("/labs", headers=b["headers"]).text
    assert "5.41" in la and "4.99" not in la
    assert "4.99" in lb and "5.41" not in lb


def test_callback_yes_commits_own_only(client, make_user, tg, canned_pdf, monkeypatch):
    monkeypatch.setattr(appmod.telegram, "get_file", lambda token, fid: "p/x.pdf")
    monkeypatch.setattr(appmod.telegram, "download_file", lambda token, p: b"%PDF-fake")
    a, b = make_user("alice"), make_user("bob")
    bot_a = connect_bot(client, tg, a, BOT_A, TG_A)
    bot_b = connect_bot(client, tg, b, BOT_B, TG_B)

    canned_pdf["value"] = 5.41
    hook_message(client, bot_a, document={"file_id": "f1", "file_name": "a.pdf"})
    kb = [s for s in tg["sent"] if s["token"] == BOT_A and s["reply_markup"]][-1]
    pid = kb["reply_markup"]["inline_keyboard"][0][0]["callback_data"].split(":", 1)[1]

    # B жмёт кнопку с pid юзера A через СВОЙ вебхук → «не нашёл» (изоляция)
    hook_callback(client, bot_b, f"pdf_yes:{pid}")
    assert "5.41" not in client.get("/labs", headers=b["headers"]).text
    # A жмёт свою — заливается
    hook_callback(client, bot_a, f"pdf_yes:{pid}")
    assert "5.41" in client.get("/labs", headers=a["headers"]).text


def test_pdf_quota(client, make_user, canned_pdf, monkeypatch):
    monkeypatch.setattr(appmod, "PDF_DAILY_QUOTA", 2)
    u = make_user("quota")
    for i in range(2):
        r = client.post("/ingest/pdf", headers=u["headers"],
                        files={"file": (f"t{i}.pdf", b"%PDF-fake", "application/pdf")})
        assert r.status_code == 200
    r = client.post("/ingest/pdf", headers=u["headers"],
                    files={"file": ("t3.pdf", b"%PDF-fake", "application/pdf")})
    assert r.status_code == 429


def test_export_telegram_needs_bot(client, make_user, canned_pdf, tg):
    u = make_user("nobot")
    r = client.post("/ingest/pdf", headers=u["headers"],
                    files={"file": ("t.pdf", b"%PDF-fake", "application/pdf")})
    client.post(f"/ingest/pdf/confirm?id={r.json()['pending_id']}", headers=u["headers"])
    r = client.get("/export/telegram?kind=analyte&id=ldl_c", headers=u["headers"])
    assert r.status_code == 409  # без бота — только скачивание файлом


def test_export_telegram_uses_own_bot(client, make_user, canned_pdf, tg):
    u = make_user("withbot")
    bot = connect_bot(client, tg, u, BOT_A, TG_A)
    r = client.post("/ingest/pdf", headers=u["headers"],
                    files={"file": ("t.pdf", b"%PDF-fake", "application/pdf")})
    client.post(f"/ingest/pdf/confirm?id={r.json()['pending_id']}", headers=u["headers"])
    r = client.get("/export/telegram?kind=analyte&id=ldl_c", headers=u["headers"])
    assert r.status_code == 200, r.text
    doc = tg["docs"][-1]
    assert doc["token"] == BOT_A and doc["chat_id"] == int(TG_A)


def test_disconnect_bot(client, make_user, tg):
    u = make_user("bye")
    connect_bot(client, tg, u, BOT_A, TG_A)
    r = client.post("/bot/disconnect", headers=u["headers"])
    assert r.status_code == 200
    assert BOT_A in tg["deleted"]
    me = client.get("/auth/me", headers=u["headers"]).json()
    assert me["bot"] == {"connected": False, "username": None, "bound": False}


def test_same_bot_cannot_join_two_accounts(client, make_user, tg):
    a, b = make_user("alice"), make_user("bob")
    assert client.post("/bot/connect", json={"token": BOT_A},
                       headers=a["headers"]).status_code == 200
    r = client.post("/bot/connect", json={"token": BOT_A}, headers=b["headers"])
    assert r.status_code == 409
