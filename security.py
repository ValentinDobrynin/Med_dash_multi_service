"""Криптопримитивы: пароли (scrypt, stdlib), токены сессий, Fernet для bot-токенов,
rate-limit логина. PLAN_multiuser v3 §6.2, §9.

Пароли — hashlib.scrypt (стандартная библиотека, без внешних зависимостей):
формат "scrypt$N$r$p$salt_hex$hash_hex". Сессии/коды — secrets.token_urlsafe,
в БД хранится только sha256-хэш.
"""
import hashlib
import hmac
import os
import secrets
import threading
import time

# --- Пароли -----------------------------------------------------------------

_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2 ** 14, 8, 1  # ~ рекомендованный интерактивный профиль


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(password.encode("utf-8"), salt=salt,
                       n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32)
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${h.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt_hex, hash_hex = stored.split("$")
        if algo != "scrypt":
            return False
        h = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt_hex),
                           n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(h.hex(), hash_hex)
    except (ValueError, TypeError):
        return False


# --- Токены (сессии, инвайты, bind-коды) ------------------------------------

def new_token(nbytes: int = 32) -> str:
    """URL-safe токен, ≥128 бит при nbytes>=16. Дефолт 32 байта = 256 бит."""
    return secrets.token_urlsafe(nbytes)


def token_hash(token: str) -> str:
    """sha256 — в БД хранится только хэш (кража БД не отдаёт живые токены)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# --- Fernet для bot-токенов (§9) ---------------------------------------------

def _fernet():
    from cryptography.fernet import Fernet
    key = os.environ.get("BOT_TOKEN_FERNET_KEY", "")
    if not key:
        raise RuntimeError("BOT_TOKEN_FERNET_KEY не задан — хранение bot-токенов невозможно")
    return Fernet(key.encode("utf-8"))


def encrypt_bot_token(token: str) -> str:
    return _fernet().encrypt(token.encode("utf-8")).decode("utf-8")


def decrypt_bot_token(enc: str) -> str:
    return _fernet().decrypt(enc.encode("utf-8")).decode("utf-8")


# --- Rate-limit логина (§6.2) -------------------------------------------------
# In-memory: один инстанс сервиса (§3.1), процесс один. Экспоненциальный backoff:
# после FREE_ATTEMPTS неудач ключ блокируется на BASE_DELAY * 2^(fails - FREE_ATTEMPTS),
# максимум MAX_DELAY. Успешный вход сбрасывает счётчик.

_FREE_ATTEMPTS = 5
_BASE_DELAY = 5.0        # секунд
_MAX_DELAY = 15 * 60.0   # 15 минут

_attempts: dict[str, tuple[int, float]] = {}  # key -> (fails, last_fail_ts)
_lock = threading.Lock()


def login_blocked_for(key: str) -> float:
    """Сколько секунд ещё ждать для ключа (ip или login). 0 = можно пробовать."""
    with _lock:
        rec = _attempts.get(key)
        if not rec:
            return 0.0
        fails, last = rec
        if fails < _FREE_ATTEMPTS:
            return 0.0
        delay = min(_BASE_DELAY * (2 ** (fails - _FREE_ATTEMPTS)), _MAX_DELAY)
        remaining = last + delay - time.monotonic()
        return max(remaining, 0.0)


def login_failed(key: str) -> None:
    with _lock:
        fails, _ = _attempts.get(key, (0, 0.0))
        _attempts[key] = (fails + 1, time.monotonic())


def login_succeeded(key: str) -> None:
    with _lock:
        _attempts.pop(key, None)


def _reset_rate_limit_for_tests() -> None:
    with _lock:
        _attempts.clear()
