# Health storage service — Docker образ (ТЗ №4).
# Причина Docker: нужен системный pdftotext (poppler-utils) для серверного разбора
# лабораторных PDF. Нативный Python-runtime Render не даёт ставить apt-пакеты.
FROM python:3.11-slim

# poppler-utils даёт pdftotext (движок настроен на его -layout вывод).
RUN apt-get update \
    && apt-get install -y --no-install-recommends poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Render передаёт порт через $PORT.
ENV PORT=8000
EXPOSE 8000

CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT}"]
