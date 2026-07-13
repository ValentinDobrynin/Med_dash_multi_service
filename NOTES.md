# NOTES — сервис хранения (ТЗ №2)

Дата сборки: 2026-07-11 (системная `date +%F`).

## Принятые решения (автономно, вопросов задать было нельзя)

1. **Семантика ответа `/ingest/weight` `{"accepted": N, "updated": K}`.**
   ТЗ не расшифровывает N/K. Принято: `accepted` = вставлено новых дат,
   `updated` = перезаписано существующих (last-write-wins). Это даёт явный
   сигнал идемпотентности: первый залив полного CSV → `{480, 0}`, повторный →
   `{0, 480}`, счётчик строк в БД не меняется. В ответе бота в Telegram — сумма
   accepted+updated (= строк в файле), как в формате «✅ N весов…» из ТЗ.

2. **`accepted` в `/ingest/labs`** = число успешно upsert-нутых строк (и insert,
   и update по natural key). Повторная заливка того же NDJSON даёт тот же
   `accepted` и не меняет `labs_count` (acceptance №1 ТЗ №2).

3. **Валидация NDJSON-строки**: обязательны `analyte_id, panel, sample_date,
   seq, source` + хотя бы одно из `value_num`/`value_text` непустое (контракт
   ТЗ №1 §2). Нарушение → строка в `rejects` с номером и причиной, остальные
   строки батча обрабатываются (частичный приём, не всё-или-ничего).

4. **Каноникализация НЕ входит в сервис**: значения/единицы сохраняются
   verbatim, никаких конверсий (г/дл ≠ г/л — как прислали, так и лежит).
   Это работа движка (ТЗ №1). Закреплено тестом `test_labs_no_canonicalization`.

5. **`/ingest/dictionary`**: принимает JSON или YAML (сначала пробуем JSON,
   иначе `yaml.safe_load`). Поддержаны формы: список объектов,
   `{"analytes": [...]}`, маппинг `analyte_id -> поля`. Full replace в одной
   транзакции; при ошибке валидации любого элемента — 400 и старый словарь
   остаётся нетронутым. Ответ `{"replaced": N}` (в ТЗ формат ответа не задан).

6. **Auth fail-closed**: если `LAB_INGEST_TOKEN` не задан в env — все
   `POST /ingest/*` отвечают 401 (а не «открыто»). `READ_TOKEN` — по ТЗ:
   не задан → GET открыты, задан → Bearer обязателен (кроме `/health`).
   Сравнение токенов — `secrets.compare_digest`.

7. **`/tg/webhook`**: если `TG_WEBHOOK_SECRET` не задан — 403 на всё
   (fail-closed, вебхук публичный). Чужой `from.id` → тихий 200 `{"ok": true}`
   без ответа в чат (не палим существование бота). Битый CSV от владельца →
   ответ в чат с ошибкой, HTTP всё равно 200 (иначе Telegram будет ретраить).
   `/tg/webhook` не под Bearer — у него свой замок (secret header + whitelist).

8. **`GET /export`** отдаёт NDJSON обеих таблиц с полем `"table"`
   (`lab_results` | `weight`), чтобы дамп был однозначно восстановим.

9. **`check_same_thread=False`** у sqlite3: FastAPI резолвит sync-dependency в
   threadpool, а async-эндпоинты работают в event loop — соединение живёт в
   рамках одного запроса и конкурентно не шарится. Один инстанс (render.yaml
   `numInstances: 1`), WAL включён.

10. **Правила версионирования из корневого CLAUDE.md** (имя_дата_vN) к файлам
    кода не применяю: это git-style проект, схема с датами в именах ломает
    импорты и деплой. Создание `service/` зафиксировано в корневых
    FILE_MAP.md / CHANGELOG.md одной записью. Исходники (TZ, CSV) оставлены
    в корне: на них ссылаются другие задачи (ТЗ №1, №3); CSV скопирован в
    `tests/fixtures/`.

11. **`telegram.py` — sync httpx** (не async): трафик — один файл в день,
    вызовы идут из async-хендлера, но блокировка на секунды несущественна;
    зато модуль тривиально мокается monkeypatch'ем в тестах.

## Результаты pytest (2026-07-11)

```
16 passed, 1 warning in 0.50s
```
(warning — внутренний DeprecationWarning starlette/anyio, не наш код)

Покрытие по требованиям:
- идемпотентность labs (двойной батч) — `test_labs_idempotent`
- вес из реального CSV (480 строк, fixtures) + идемпотентность + last-write-wins — `test_weight_from_real_csv`
- reject незамапленного analyte_id — `test_labs_reject_unmapped_analyte`
- нет каноникализации — `test_labs_no_canonicalization`
- auth 401 (нет/неверный токен), READ_TOKEN опционален — `test_auth.py` (4 теста)
- webhook: неверный секрет → 403, чужой user → тихий игнор, подсказка на текст,
  валидный CSV-документ с мокнутыми getFile/download/sendMessage → upsert +
  «✅ N весов…» — `test_webhook.py` (4 теста)
- фильтры и join `/labs`, full replace словаря + YAML, `/export` — остальные

Тесты в сеть не ходят: Telegram мокается через monkeypatch модуля `telegram`,
БД — tmp-файл per-test.

## Smoke (локально, uvicorn :8871, DB /tmp/health_test.db, 2026-07-11)

```
GET /health (пустая БД)
  {"status":"ok","labs_count":0,"weight_count":0,"analytes_count":0,"last_ingest":null}

POST /ingest/dictionary (2 маркера: ldl_c, ferritin)
  {"replaced":2}

POST /ingest/labs (3 NDJSON-строки)
  {"accepted":3,"rejected":0,"rejects":[]}

POST /ingest/labs (тот же батч повторно — идемпотентность)
  {"accepted":3,"rejected":0,"rejects":[]}     # labs_count остался 3

GET /labs?panel=lipids_cardio
  2 строки ldl_c (2026-05-18: 4.88; 2026-06-20: 5.1),
  приджойнено name_ru="ЛПНП", direction="higher_worse", value_type="quantitative"

POST /ingest/weight (реальный WeightDrop-Export-2026-07-11.csv, 480 строк)
  {"accepted":480,"updated":0}
повторно:
  {"accepted":0,"updated":480}                 # weight_count остался 480

POST /ingest/labs без токена
  HTTP 401

GET /health (финал)
  {"status":"ok","labs_count":3,"weight_count":480,"analytes_count":2,
   "last_ingest":"2026-07-11T07:14:19Z"}
```

Процесс uvicorn убит, /tmp/health_test.db удалён, порт 8871 закрыт (проверено).

## Что осталось на деплой (вне кода)

- Задать секреты в Render dashboard (все `sync: false` в render.yaml).
- Разово вызвать `setWebhook` с `secret_token` (команда в README.md).
- Persistent disk `/data` подтвердить после первого redeploy (acceptance №5).
