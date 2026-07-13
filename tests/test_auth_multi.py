# -*- coding: utf-8 -*-
"""M4: регистрация по инвайту, логин, rate-limit, смена/сброс пароля."""
from conftest import ADMIN_HEADERS


def test_register_requires_valid_invite(client):
    r = client.post("/auth/register", json={
        "code": "nope", "login": "x", "name": "X", "password": "12345678", "consent": True})
    assert r.status_code == 400


def test_register_requires_consent(client):
    r = client.post("/admin/invites", headers=ADMIN_HEADERS, json={})
    code = r.json()["code"]
    r = client.post("/auth/register", json={
        "code": code, "login": "x", "name": "X", "password": "12345678", "consent": False})
    assert r.status_code == 400


def test_invite_is_single_use(client, make_user):
    r = client.post("/admin/invites", headers=ADMIN_HEADERS, json={})
    code = r.json()["code"]
    r1 = client.post("/auth/register", json={
        "code": code, "login": "first", "name": "F", "password": "12345678", "consent": True})
    assert r1.status_code == 200
    client.cookies.clear()
    r2 = client.post("/auth/register", json={
        "code": code, "login": "second", "name": "S", "password": "12345678", "consent": True})
    assert r2.status_code == 400


def test_login_logout_me(client, make_user):
    u = make_user("valentin")
    r = client.get("/auth/me", headers=u["headers"])
    assert r.status_code == 200
    assert r.json()["login"] == "valentin"
    assert r.json()["bot"] == {"connected": False, "username": None, "bound": False}

    r = client.post("/auth/login", json={"login": "valentin", "password": u["password"]})
    assert r.status_code == 200
    client.cookies.clear()

    r = client.post("/auth/logout", headers=u["headers"])
    assert r.status_code == 200
    r = client.get("/auth/me", headers=u["headers"])
    assert r.status_code == 401  # сессия убита


def test_login_rate_limit(client, make_user):
    make_user("bruce")
    for _ in range(5):
        r = client.post("/auth/login", json={"login": "bruce", "password": "wrong-pass"})
        assert r.status_code == 401
    r = client.post("/auth/login", json={"login": "bruce", "password": "wrong-pass"})
    assert r.status_code == 429  # экспоненциальный backoff включился


def test_change_password(client, make_user):
    u = make_user("carol")
    r = client.post("/auth/change_password", headers=u["headers"],
                    json={"old": "wrong", "new": "new-password-1"})
    assert r.status_code == 403
    r = client.post("/auth/change_password", headers=u["headers"],
                    json={"old": u["password"], "new": "new-password-1"})
    assert r.status_code == 200
    r = client.post("/auth/login", json={"login": "carol", "password": "new-password-1"})
    assert r.status_code == 200


def test_reset_flow_kills_old_sessions(client, make_user):
    u = make_user("dave")
    r = client.post("/admin/reset_code", headers=ADMIN_HEADERS,
                    json={"user_id": u["user_id"]})
    assert r.status_code == 200
    code = r.json()["code"]
    r = client.post("/auth/reset_password", json={"code": code, "new": "fresh-password"})
    assert r.status_code == 200
    client.cookies.clear()
    # старая сессия мертва
    assert client.get("/auth/me", headers=u["headers"]).status_code == 401
    # новый пароль работает
    assert client.post("/auth/login",
                       json={"login": "dave", "password": "fresh-password"}).status_code == 200
    # код одноразовый
    assert client.post("/auth/reset_password",
                       json={"code": code, "new": "another-pass1"}).status_code == 400


def test_data_endpoints_require_session(client):
    for path in ("/labs", "/weight", "/dictionary", "/export", "/auth/me"):
        assert client.get(path).status_code == 401, path
    assert client.post("/weight/entry", json={}).status_code == 401
