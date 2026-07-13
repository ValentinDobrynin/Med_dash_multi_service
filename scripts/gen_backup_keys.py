#!/usr/bin/env python3
"""Генерация ключевой пары для шифрованных бэкапов (запускается ЛОКАЛЬНО у админа).

Приватный ключ (backup_private.pem) НИКОГДА не попадает на Render — храни локально
и в надёжном месте. Публичный (backup_public.pem) → env BACKUP_PUBLIC_KEY_PEM на Render.
"""
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

key = rsa.generate_private_key(public_exponent=65537, key_size=4096)

Path("backup_private.pem").write_bytes(key.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
))
Path("backup_public.pem").write_bytes(key.public_key().public_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PublicFormat.SubjectPublicKeyInfo,
))
print("Создано: backup_private.pem (ХРАНИТЬ ЛОКАЛЬНО), backup_public.pem (→ Render env)")
