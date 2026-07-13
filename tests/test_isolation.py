# -*- coding: utf-8 -*-
"""M6 — БЛОКЕР РЕЛИЗА: тест изоляции тенантов по реестру data-эндпоинтов (§3.2 п.6).

Реестр всех эндпоинтов, отдающих пользовательские данные, задан явно списком;
новый data-эндпоинт обязан быть добавлен сюда (правило реестра).
"""
import json

from conftest import ADMIN_HEADERS


# Реестр data-эндпоинтов (§3.2 п.6). GET, сессионные.
DATA_ENDPOINTS = ["/labs", "/weight", "/export"]

A_VALUE, B_VALUE = 5.41, 4.99  # различимые значения ЛПНП юзеров A и B


def _seed_two_users(client, make_user, canned_pdf):
    a = make_user("alice")
    b = make_user("bob")
    # лабы через PDF-флоу дэша (превью → confirm)
    for u, val, w in ((a, A_VALUE, 61.5), (b, B_VALUE, 88.8)):
        canned_pdf["value"] = val
        r = client.post("/ingest/pdf", headers=u["headers"],
                        files={"file": ("t.pdf", b"%PDF-fake", "application/pdf")})
        assert r.status_code == 200, r.text
        pid = r.json()["pending_id"]
        assert pid
        u["pending_id"] = pid
        r = client.post(f"/ingest/pdf/confirm?id={pid}", headers=u["headers"])
        assert r.status_code == 200, r.text
        # вес с дэша (§5.5)
        r = client.post("/weight/entry", headers=u["headers"],
                        json={"measure_date": "2026-01-15", "weight_kg": w})
        assert r.status_code == 200, r.text
    return a, b


def test_isolation_across_registry(client, make_user, canned_pdf):
    a, b = _seed_two_users(client, make_user, canned_pdf)
    # какой эндпоинт какие значения отдаёт: (список «своих», список «чужих не должно быть»)
    expectations = {
        "/labs":   (str(A_VALUE), str(B_VALUE)),
        "/weight": ("61.5", "88.8"),
        "/export": (str(A_VALUE), str(B_VALUE)),
    }
    for path in DATA_ENDPOINTS:
        ra = client.get(path, headers=a["headers"])
        rb = client.get(path, headers=b["headers"])
        assert ra.status_code == 200 and rb.status_code == 200, path
        ta, tb = ra.text, rb.text
        val_a, val_b = expectations[path]
        assert val_a in ta and val_a not in tb, f"{path}: утечка A→B"
        assert val_b in tb and val_b not in ta, f"{path}: утечка B→A"
    # /export несёт и вес — сверяем отдельно
    ta = client.get("/export", headers=a["headers"]).text
    tb = client.get("/export", headers=b["headers"]).text
    assert "61.5" in ta and "61.5" not in tb, "/export: вес A утёк"
    assert "88.8" in tb and "88.8" not in ta, "/export: вес B утёк"


def test_pdf_report_isolated(client, make_user, canned_pdf):
    a, b = _seed_two_users(client, make_user, canned_pdf)
    ra = client.get("/export/report?kind=analyte&id=ldl_c", headers=a["headers"])
    assert ra.status_code == 200
    assert ra.headers["content-type"] == "application/pdf"
    # у юзера без данных по маркеру — 404, а не чужой отчёт
    c = make_user("carol")
    rc = client.get("/export/report?kind=analyte&id=ldl_c", headers=c["headers"])
    assert rc.status_code == 404


def test_cross_confirm_forbidden(client, make_user, canned_pdf):
    """Юзер B не может подтвердить/отменить pending юзера A."""
    a, b = _seed_two_users(client, make_user, canned_pdf)
    canned_pdf["value"] = 7.77
    r = client.post("/ingest/pdf", headers=a["headers"],
                    files={"file": ("x.pdf", b"%PDF-fake", "application/pdf")})
    pid = r.json()["pending_id"]
    assert client.post(f"/ingest/pdf/confirm?id={pid}", headers=b["headers"]).status_code == 404
    assert client.post(f"/ingest/pdf/cancel?id={pid}", headers=b["headers"]).status_code == 404
    # свой — работает
    assert client.post(f"/ingest/pdf/confirm?id={pid}", headers=a["headers"]).status_code == 200
    # 7.77 не появился у B
    assert "7.77" not in client.get("/labs", headers=b["headers"]).text


def test_admin_reset_labs_requires_user_id(client, make_user, canned_pdf):
    """§9: destructive-операции только адресные, режима «все» не существует."""
    a, b = _seed_two_users(client, make_user, canned_pdf)
    r = client.post("/admin/reset_labs", headers=ADMIN_HEADERS)
    assert r.status_code == 422  # user_id обязателен
    r = client.post(f"/admin/reset_labs?user_id={a['user_id']}", headers=ADMIN_HEADERS)
    assert r.status_code == 200
    assert client.get("/labs", headers=a["headers"]).json() == []
    assert str(B_VALUE) in client.get("/labs", headers=b["headers"]).text  # B цел


def test_health_leaks_nothing(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}  # никаких счётчиков публично (§9)


def test_no_store_on_responses(client, make_user):
    """§3.2 п.5: транспортный слой — no-store на каждом ответе."""
    u = make_user("cachey")
    for path in ("/health", "/labs", "/dictionary"):
        r = client.get(path, headers=u["headers"])
        assert r.headers.get("Cache-Control") == "private, no-store", path


def test_admin_users_has_no_secrets(client, make_user, tg):
    u = make_user("sec")
    client.post("/bot/connect", json={"token": "42:SECRET-BOT-TOKEN"}, headers=u["headers"])
    r = client.get("/admin/users", headers=ADMIN_HEADERS)
    assert r.status_code == 200
    body = r.text
    assert "SECRET-BOT-TOKEN" not in body
    assert "password" not in body.lower() or "password_hash" not in body
    rec = [x for x in r.json() if x["login"] == "sec"][0]
    assert rec["bot_connected"] is True
    assert set(rec.keys()) <= {"user_id", "login", "name", "role", "status", "created_at",
                               "bot_connected", "bot_username", "bot_bound",
                               "labs_count", "weight_count"}


def test_export_is_attachment_and_own_only(client, make_user, canned_pdf):
    a, b = _seed_two_users(client, make_user, canned_pdf)
    r = client.get("/export", headers=a["headers"])
    assert "attachment" in r.headers.get("content-disposition", "")
    lines = [json.loads(x) for x in r.text.strip().splitlines()]
    assert all("user_id" not in x for x in lines)  # свои и так — id наружу не светим
    tables = {x["table"] for x in lines}
    assert tables == {"lab_results", "weight"}
