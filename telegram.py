"""Telegram Bot API — мультитенантная версия (PLAN_multiuser v3 §5.4).

Токен бота — ОБЯЗАТЕЛЬНЫЙ первый аргумент каждого вызова; глобального
WEIGHT_BOT_TOKEN не существует. Токен приходит из users конкретного юзера
(расшифровка Fernet на лету в app.py). Тесты монкипатчат эти функции.
"""
import httpx

API_BASE = "https://api.telegram.org"
_TIMEOUT = 30.0


def get_me(token: str) -> dict:
    """getMe: валидация токена при подключении бота. Возвращает result-объект."""
    r = httpx.get(f"{API_BASE}/bot{token}/getMe", timeout=_TIMEOUT)
    r.raise_for_status()
    return r.json()["result"]


def set_webhook(token: str, url: str, secret_token: str) -> None:
    r = httpx.post(
        f"{API_BASE}/bot{token}/setWebhook",
        json={"url": url, "secret_token": secret_token,
              "allowed_updates": ["message", "callback_query"]},
        timeout=_TIMEOUT,
    )
    r.raise_for_status()


def delete_webhook(token: str) -> None:
    r = httpx.post(f"{API_BASE}/bot{token}/deleteWebhook", timeout=_TIMEOUT)
    r.raise_for_status()


def get_webhook_info(token: str) -> dict:
    r = httpx.get(f"{API_BASE}/bot{token}/getWebhookInfo", timeout=_TIMEOUT)
    r.raise_for_status()
    return r.json()["result"]


def get_file(token: str, file_id: str) -> str:
    """getFile: file_id -> file_path on Telegram's file server."""
    r = httpx.get(
        f"{API_BASE}/bot{token}/getFile",
        params={"file_id": file_id},
        timeout=_TIMEOUT,
    )
    r.raise_for_status()
    return r.json()["result"]["file_path"]


def download_file(token: str, file_path: str) -> bytes:
    r = httpx.get(f"{API_BASE}/file/bot{token}/{file_path}", timeout=_TIMEOUT)
    r.raise_for_status()
    return r.content


def send_message(token: str, chat_id: int, text: str,
                 reply_markup: dict | None = None) -> None:
    payload = {"chat_id": chat_id, "text": text}
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    r = httpx.post(f"{API_BASE}/bot{token}/sendMessage", json=payload, timeout=_TIMEOUT)
    r.raise_for_status()


def answer_callback_query(token: str, callback_query_id: str, text: str = "") -> None:
    """Гасит «часики» на инлайн-кнопке после нажатия."""
    payload = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
    r = httpx.post(f"{API_BASE}/bot{token}/answerCallbackQuery",
                   json=payload, timeout=_TIMEOUT)
    r.raise_for_status()


def edit_message_text(token: str, chat_id: int, message_id: int, text: str,
                      reply_markup: dict | None = None) -> None:
    """Меняет текст сообщения и (опц.) убирает кнопки — после выбора Да/Нет."""
    payload = {"chat_id": chat_id, "message_id": message_id, "text": text}
    payload["reply_markup"] = reply_markup if reply_markup is not None else {"inline_keyboard": []}
    r = httpx.post(f"{API_BASE}/bot{token}/editMessageText",
                   json=payload, timeout=_TIMEOUT)
    r.raise_for_status()


def send_document(token: str, chat_id: int, content: bytes, filename: str,
                  caption: str = "", mime: str = "application/pdf") -> None:
    """sendDocument: шлёт файл (PDF-отчёт, шифрованный бэкап) в чат с ботом."""
    files = {"document": (filename, content, mime)}
    data = {"chat_id": str(chat_id)}
    if caption:
        data["caption"] = caption
    r = httpx.post(f"{API_BASE}/bot{token}/sendDocument",
                   data=data, files=files, timeout=60.0)
    r.raise_for_status()


def set_my_commands(token: str, commands: list[dict]) -> None:
    r = httpx.post(f"{API_BASE}/bot{token}/setMyCommands",
                   json={"commands": commands}, timeout=_TIMEOUT)
    r.raise_for_status()
