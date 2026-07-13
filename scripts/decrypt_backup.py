#!/usr/bin/env python3
"""Расшифровка бэкапа (ЛОКАЛЬНО у админа): decrypt_backup.py <file.backup.enc> [private.pem]

Выход: <file>.sql — SQL-дамп; восстановление: sqlite3 health.db < file.sql
"""
import struct
import sys
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

enc_path = Path(sys.argv[1])
key_path = Path(sys.argv[2] if len(sys.argv) > 2 else "backup_private.pem")

blob = enc_path.read_bytes()
(klen,) = struct.unpack(">I", blob[:4])
enc_key, nonce, ciphertext = blob[4:4 + klen], blob[4 + klen:4 + klen + 12], blob[4 + klen + 12:]

private_key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
aes_key = private_key.decrypt(
    enc_key,
    padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()),
                 algorithm=hashes.SHA256(), label=None),
)
data = AESGCM(aes_key).decrypt(nonce, ciphertext, None)

out = enc_path.with_suffix(".sql")
out.write_bytes(data)
print(f"OK: {out} ({len(data)} байт). Восстановление: sqlite3 health.db < {out.name}")
