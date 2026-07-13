"""Шифрованные бэкапы (PLAN_multiuser v3 §9).

Гибридная схема: данные шифруются одноразовым AES-256-GCM ключом, сам ключ —
RSA-OAEP публичным ключом из env BACKUP_PUBLIC_KEY_PEM. Приватный ключ хранится
ТОЛЬКО локально у админа (scripts/gen_backup_keys.py / decrypt_backup.py) —
Render расшифровать бэкап не может.

Формат файла: 4 байта длины RSA-блока (big-endian) + RSA(aes_key) + nonce(12) + ciphertext.

Назначения (M1: «оба»):
- Telegram: админ-юзеру (role=admin, привязанный бот) — send_document;
- S3/R2: если заданы S3_ENDPOINT/S3_BUCKET/S3_ACCESS_KEY/S3_SECRET_KEY (boto3).
"""
import io
import os
import sqlite3
import struct
from datetime import datetime, timezone

import db


class BackupNotConfigured(RuntimeError):
    pass


def _now_tag() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")


def _dump_db_bytes() -> bytes:
    """Консистентный дамп SQLite через backup API (не копия файла под записью)."""
    src = db.connect()
    try:
        dst = sqlite3.connect(":memory:")
        src.backup(dst)
        buf = io.BytesIO()
        for line in dst.iterdump():
            buf.write((line + "\n").encode("utf-8"))
        dst.close()
        return buf.getvalue()
    finally:
        src.close()


def encrypt_backup(data: bytes) -> bytes:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    pem = os.environ.get("BACKUP_PUBLIC_KEY_PEM", "")
    if not pem:
        raise BackupNotConfigured("BACKUP_PUBLIC_KEY_PEM не задан — бэкап без шифрования запрещён (§9)")
    public_key = serialization.load_pem_public_key(pem.encode("utf-8"))

    aes_key = os.urandom(32)
    nonce = os.urandom(12)
    ciphertext = AESGCM(aes_key).encrypt(nonce, data, None)
    enc_key = public_key.encrypt(
        aes_key,
        padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()),
                     algorithm=hashes.SHA256(), label=None),
    )
    return struct.pack(">I", len(enc_key)) + enc_key + nonce + ciphertext


def _send_to_telegram(blob: bytes, filename: str) -> bool:
    """Шлёт бэкап админ-юзеру через ЕГО бота (в системе нет глобального бота)."""
    import security
    import telegram
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT * FROM users WHERE role = 'admin' AND bot_token_enc IS NOT NULL"
            " AND tg_user_id IS NOT NULL ORDER BY created_at LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return False
    token = security.decrypt_bot_token(row["bot_token_enc"])
    telegram.send_document(token, int(row["tg_user_id"]), blob, filename,
                           caption=f"encrypted backup {_now_tag()}",
                           mime="application/octet-stream")
    return True


def _send_to_s3(blob: bytes, filename: str) -> bool:
    endpoint = os.environ.get("S3_ENDPOINT", "")
    bucket = os.environ.get("S3_BUCKET", "")
    access = os.environ.get("S3_ACCESS_KEY", "")
    secret = os.environ.get("S3_SECRET_KEY", "")
    if not (bucket and access and secret):
        return False
    import boto3
    client = boto3.client(
        "s3", endpoint_url=(endpoint or None),
        aws_access_key_id=access, aws_secret_access_key=secret,
    )
    client.put_object(Bucket=bucket, Key=f"backups/{filename}", Body=blob)
    return True


def run_backup() -> dict:
    data = _dump_db_bytes()
    blob = encrypt_backup(data)
    filename = f"health_multi_{_now_tag()}.backup.enc"
    sent_tg = sent_s3 = False
    errors = []
    try:
        sent_tg = _send_to_telegram(blob, filename)
    except Exception as e:  # noqa: BLE001
        errors.append(f"telegram: {e}")
    try:
        sent_s3 = _send_to_s3(blob, filename)
    except Exception as e:  # noqa: BLE001
        errors.append(f"s3: {e}")
    if not sent_tg and not sent_s3:
        raise BackupNotConfigured(
            "ни один канал не сработал: " + ("; ".join(errors) or "не настроены TG-админ и S3"))
    return {"filename": filename, "size": len(blob),
            "sent_telegram": sent_tg, "sent_s3": sent_s3, "errors": errors}
