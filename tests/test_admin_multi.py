# -*- coding: utf-8 -*-
"""M17–M21: адресные админ-операции, удаление юзера, бэкфилл, шифрованный бэкап."""
import json
import struct

from conftest import ADMIN_HEADERS, connect_bot

NDJSON = "\n".join([
    json.dumps({"analyte_id": "ldl_c", "panel": "lipids_cardio",
                "sample_date": "2025-03-01", "seq": 0, "value_num": 6.1,
                "unit": "ммоль/л", "source": "backfill"}),
    json.dumps({"analyte_id": "nope_marker", "panel": "x",
                "sample_date": "2025-03-01", "seq": 0, "value_num": 1,
                "source": "backfill"}),
])


def test_admin_requires_token_or_admin_session(client, make_user):
    u = make_user("pleb")
    assert client.get("/admin/users").status_code == 401
    assert client.get("/admin/users", headers=u["headers"]).status_code == 401
    assert client.get("/admin/users", headers=ADMIN_HEADERS).status_code == 200


def test_backfill_labs_per_user(client, make_user):
    a, b = make_user("alice"), make_user("bob")
    r = client.post(f"/admin/backfill/labs?user_id={a['user_id']}",
                    headers=ADMIN_HEADERS, content=NDJSON.encode())
    assert r.status_code == 200
    body = r.json()
    assert body["accepted"] == 1 and body["rejected"] == 1  # unmapped → reject
    assert "6.1" in client.get("/labs", headers=a["headers"]).text
    assert client.get("/labs", headers=b["headers"]).json() == []
    # без user_id — 422
    assert client.post("/admin/backfill/labs", headers=ADMIN_HEADERS,
                       content=b"{}").status_code == 422


def test_delete_user_full_wipe(client, make_user, tg, canned_pdf):
    u = make_user("gone")
    connect_bot(client, tg, u, "333:CCC", "70003")
    r = client.post("/ingest/pdf", headers=u["headers"],
                    files={"file": ("t.pdf", b"%PDF-fake", "application/pdf")})
    client.post(f"/ingest/pdf/confirm?id={r.json()['pending_id']}", headers=u["headers"])
    client.post("/weight/entry", headers=u["headers"],
                json={"measure_date": "2026-01-01", "weight_kg": 90})

    # неверный confirm
    r = client.delete(f"/admin/user?user_id={u['user_id']}&confirm=wrong",
                      headers=ADMIN_HEADERS)
    assert r.status_code == 400
    # верный: confirm = login
    r = client.delete(f"/admin/user?user_id={u['user_id']}&confirm=gone",
                      headers=ADMIN_HEADERS)
    assert r.status_code == 200
    deleted = r.json()["deleted"]
    assert deleted["users"] == 1 and deleted["lab_results"] > 0 and deleted["weight"] == 1
    assert "333:CCC" in tg["deleted"]          # вебхук бота снят
    assert client.get("/auth/me", headers=u["headers"]).status_code == 401  # сессии убиты
    users = client.get("/admin/users", headers=ADMIN_HEADERS).json()
    assert all(x["login"] != "gone" for x in users)


def test_overview_counts(client, make_user, canned_pdf):
    u = make_user("stats")
    r = client.post("/ingest/pdf", headers=u["headers"],
                    files={"file": ("t.pdf", b"%PDF-fake", "application/pdf")})
    client.post(f"/ingest/pdf/confirm?id={r.json()['pending_id']}", headers=u["headers"])
    ov = client.get("/admin/overview", headers=ADMIN_HEADERS).json()
    assert ov["users"] == 1 and ov["labs_count"] == 2


def test_rejects_view(client, make_user, canned_pdf, monkeypatch):
    import app as appmod
    from conftest import canned_preview
    u = make_user("rej")
    p = canned_preview()
    p["rejects"] = [{"name": "Загадочный маркер", "reason": "unmapped"}]
    monkeypatch.setattr(appmod, "_build_pdf_preview", lambda d, n: (p, None))
    client.post("/ingest/pdf", headers=u["headers"],
                files={"file": ("t.pdf", b"%PDF-fake", "application/pdf")})
    r = client.get(f"/admin/rejects?user_id={u['user_id']}", headers=ADMIN_HEADERS)
    assert r.status_code == 200
    assert r.json() and r.json()[0]["rejects"][0]["name"] == "Загадочный маркер"


def test_backup_encrypted_roundtrip(client, make_user, tg, monkeypatch):
    """§9: бэкап шифруется гибридно; расшифровка возможна только приватным ключом."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    # без публичного ключа — 503 (незашифрованный бэкап запрещён)
    r = client.post("/admin/backup", headers=ADMIN_HEADERS)
    assert r.status_code == 503

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pub_pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    monkeypatch.setenv("BACKUP_PUBLIC_KEY_PEM", pub_pem)

    # админ-юзер с ботом — канал доставки
    import sqlite3
    import db as dbmod
    admin_u = make_user("boss")
    conn = dbmod.connect()
    conn.execute("UPDATE users SET role='admin' WHERE login='boss'")
    conn.commit()
    conn.close()
    connect_bot(client, tg, admin_u, "999:ADMIN", "70099")

    r = client.post("/admin/backup", headers=ADMIN_HEADERS)
    assert r.status_code == 200, r.text
    assert r.json()["sent_telegram"] is True

    blob = tg["docs"][-1]["content"]
    (klen,) = struct.unpack(">I", blob[:4])
    enc_key, nonce, ct = blob[4:4 + klen], blob[4 + klen:4 + klen + 12], blob[4 + klen + 12:]
    aes_key = key.decrypt(enc_key, padding.OAEP(
        mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
    sql = AESGCM(aes_key).decrypt(nonce, ct, None).decode()
    assert "CREATE TABLE" in sql and "users" in sql
