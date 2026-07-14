"""SQLite layer: connection factory + multi-tenant schema (PLAN_multiuser v3 §4).

Отличия от single-tenant: user_id входит в PK всех пользовательских таблиц;
таблицы users / invites / sessions; analyte_meta остаётся глобальной (§3.3).
Схема портируемая (без SQLite-специфики в запросах приложения).
"""
import os
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id        TEXT PRIMARY KEY,               -- uuid4
    login          TEXT UNIQUE NOT NULL,
    name           TEXT NOT NULL,
    password_hash  TEXT NOT NULL,                  -- scrypt (security.py)
    role           TEXT NOT NULL DEFAULT 'user',   -- user | admin
    status         TEXT NOT NULL DEFAULT 'active', -- active | disabled
    -- Telegram-бот юзера (1:1, опционален; NULL пока не подключён):
    bot_token_enc  TEXT,                           -- Fernet-шифрованный токен
    bot_username   TEXT,
    webhook_secret TEXT,
    tg_user_id     TEXT,                           -- заполняется ТОЛЬКО bind-флоу
    bind_code_hash TEXT,                           -- хэш одноразового кода привязки
    bind_code_expires TEXT,                        -- ISO; NULL = кода нет
    created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS invites (
    code       TEXT PRIMARY KEY,                   -- ≥128 бит энтропии
    kind       TEXT NOT NULL DEFAULT 'invite',     -- invite | reset
    for_user   TEXT,                               -- для kind=reset: чей пароль
    note       TEXT,
    created_at TEXT NOT NULL,
    expires_at TEXT,
    used_by    TEXT REFERENCES users(user_id)
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,                   -- sha256 токена, не сам токен
    user_id    TEXT NOT NULL REFERENCES users(user_id),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    last_seen  TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);

CREATE TABLE IF NOT EXISTS lab_results (
    user_id      TEXT    NOT NULL,
    analyte_id   TEXT    NOT NULL,
    panel        TEXT    NOT NULL,
    sample_date  TEXT    NOT NULL,
    seq          INTEGER NOT NULL DEFAULT 0,
    value_num    REAL,
    value_text   TEXT,
    unit         TEXT,
    ref_low      REAL,
    ref_high     REAL,
    ref_raw      TEXT,
    source       TEXT    NOT NULL,
    ingested_at  TEXT    NOT NULL,
    PRIMARY KEY (user_id, analyte_id, sample_date, seq)
);
CREATE INDEX IF NOT EXISTS idx_lab_user_panel   ON lab_results(user_id, panel);
CREATE INDEX IF NOT EXISTS idx_lab_user_date    ON lab_results(user_id, sample_date);
CREATE INDEX IF NOT EXISTS idx_lab_user_analyte ON lab_results(user_id, analyte_id, sample_date);

CREATE TABLE IF NOT EXISTS weight (
    user_id      TEXT NOT NULL,
    measure_date TEXT NOT NULL,
    weight_kg    REAL NOT NULL,
    note         TEXT,
    source       TEXT NOT NULL DEFAULT 'WeightDrop',
    ingested_at  TEXT NOT NULL,
    PRIMARY KEY (user_id, measure_date)
);

CREATE TABLE IF NOT EXISTS analyte_meta (
    analyte_id     TEXT PRIMARY KEY,
    name_ru        TEXT NOT NULL,
    panel          TEXT NOT NULL,
    unit_canonical TEXT,
    value_type     TEXT NOT NULL,
    direction      TEXT NOT NULL
);

-- Превью распознанного PDF ДО подтверждения. Все выборки — с user_id (§3.2 п.1).
CREATE TABLE IF NOT EXISTS pending_uploads (
    id           TEXT PRIMARY KEY,       -- uuid4
    user_id      TEXT NOT NULL,
    source       TEXT NOT NULL,          -- telegram | dash
    chat_id      TEXT,                   -- для бота: кому отвечать
    filename     TEXT,
    created_at   TEXT NOT NULL,
    summary_json TEXT NOT NULL,
    rows_json    TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending'  -- pending | committed | cancelled
);
CREATE INDEX IF NOT EXISTS idx_pending_user_chat ON pending_uploads(user_id, chat_id, status, created_at);
CREATE INDEX IF NOT EXISTS idx_pending_user_day  ON pending_uploads(user_id, created_at);

-- Заявки на доступ через бота-вахтёра (authbot.py): новый человек пишет боту →
-- admin подтверждает кнопкой → бот шлёт инвайт-ссылку. tg_id — Telegram-id заявителя.
CREATE TABLE IF NOT EXISTS access_requests (
    id          TEXT PRIMARY KEY,       -- uuid4
    tg_id       TEXT NOT NULL,          -- Telegram-id заявителя (= chat_id в личке)
    tg_username TEXT,
    name        TEXT,                   -- «Имя Фамилия», введённое заявителем
    status      TEXT NOT NULL DEFAULT 'awaiting_name', -- awaiting_name|pending|approved|rejected
    invite_code TEXT,                   -- заполняется при одобрении
    created_at  TEXT NOT NULL,
    decided_at  TEXT,
    decided_by  TEXT                    -- Telegram-id админа, принявшего решение
);
CREATE INDEX IF NOT EXISTS idx_access_req_tg ON access_requests(tg_id, status, created_at);
"""


def db_path() -> str:
    return os.environ.get("DB_PATH", "./data/health.db")


def connect() -> sqlite3.Connection:
    path = db_path()
    Path(path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def seed_dictionary(conn: sqlite3.Connection) -> int:
    """Авто-seed глобального словаря из lab_engine/analyte_dictionary.yaml.

    INSERT OR IGNORE на каждом старте (механизм p2): добивает новые маркеры при
    передеплое, существующие записи не трогает.
    """
    import yaml
    path = Path(__file__).resolve().parent / "lab_engine" / "analyte_dictionary.yaml"
    if not path.exists():
        return 0
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    items = data.get("analytes") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return 0
    n = 0
    for it in items:
        if not isinstance(it, dict) or not it.get("analyte_id"):
            continue
        cur = conn.execute(
            """INSERT OR IGNORE INTO analyte_meta
                 (analyte_id, name_ru, panel, unit_canonical, value_type, direction)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                it["analyte_id"], it.get("name_ru") or it["analyte_id"],
                it.get("panel") or "other_markers", it.get("unit_canonical"),
                it.get("value_type") or "quantitative",
                it.get("direction") or "informational",
            ),
        )
        n += cur.rowcount
    conn.commit()
    return n


def init_db() -> None:
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        conn.commit()
        seed_dictionary(conn)
    finally:
        conn.close()
