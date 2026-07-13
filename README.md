# health-multi — мультитенантный сервис хранения

FastAPI + SQLite (PLAN_multiuser v3). Десятки юзеров в одной БД: user_id в PK
всех пользовательских таблиц, сессии (httpOnly cookie), инвайты, per-user
Telegram-боты (`/tg/webhook/{user_id}`, токены шифруются Fernet), квоты
(50 PDF/день, 25 МБ), шифрованные бэкапы (RSA+AES-GCM, приватный ключ только
у админа), адресные админ-операции.

Авторизация по путям (§6.3):
- юзерские данные и загрузка — сессия (user_id ТОЛЬКО из сессии);
- бот — webhook_secret из пути;
- админ-канал — `LAB_INGEST_TOKEN` или сессия role=admin.

## Локальный запуск

```bash
pip install -r requirements.txt
export DB_PATH=./data/health.db LAB_INGEST_TOKEN=dev-token COOKIE_SECURE=0 \
       PUBLIC_URL=http://localhost:8000 \
       BOT_TOKEN_FERNET_KEY=$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())") \
       ADMIN_LOGIN=admin ADMIN_PASSWORD=dev-password
uvicorn app:app --reload
```

## Тесты

```bash
python3 -m pytest tests/   # 47: auth, ИЗОЛЯЦИЯ (блокер релиза), боты крест-накрест,
                           # админ, бэкап-раундтрип, фиделити парсера
```

Реестр data-эндпоинтов — `tests/test_isolation.py::DATA_ENDPOINTS`; ревизия
каждого эндпоинта — `../ENDPOINT_AUDIT.md`. Деплой — `../DEPLOY_MULTI.md`.

## Структура

- `app.py` — все эндпоинты (auth / bot / ingest / read / admin / webhook)
- `auth.py` — сессии + админ-токен · `security.py` — scrypt, токены, Fernet, rate-limit
- `db.py` — мультитенантная схема + авто-seed словаря
- `telegram.py` — Bot API, токен = обязательный параметр каждого вызова (§5.4)
- `backup.py` + `scripts/gen_backup_keys.py`, `scripts/decrypt_backup.py`
- `lab_engine/` — вендоренный парсер (копия №4; правки — во все копии, §3.4)
