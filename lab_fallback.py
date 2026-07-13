# -*- coding: utf-8 -*-
"""ТЗ №4 Э3: fallback-извлечение лаб-показателей через Claude API.

Вызывается ТОЛЬКО когда базовый регэксп-парсер слаб (нет даты / мало значений) и
задан ANTHROPIC_API_KEY. Возвращает список сырых параметров в формате движка
({name, raw_name, value, unit, ref}) + дату — их канонизирует общий canonicalize
(резолв имён через словарь, незамапленное → reject). Медданные наружу не уходят,
кроме самого текста анализа в API-запросе (осознанно, только на fallback).

Публичное:
  is_enabled() -> bool
  extract(text) -> {"date": str|None, "params": [ {name,raw_name,value,unit,ref} ]}
"""
from __future__ import annotations
import json
import os
import re

import httpx

API_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-sonnet-5"
API_VERSION = "2023-06-01"

_SYS = (
    "Ты извлекаешь табличные лабораторные показатели из текста медицинского анализа. "
    "Верни СТРОГО JSON без пояснений в форме "
    '{"date":"YYYY-MM-DD"|null,"params":[{"name":str,"value":str,"unit":str,"ref":str}]}. '
    "date — дата ЗАБОРА биоматериала (не выдачи). Для каждого показателя: name как в "
    "документе (рус/лат), value — число как строка (десятичная точка или запятая, "
    "префиксы <,> сохрани), unit — единица (пустая строка если нет), ref — референсный "
    "интервал строкой ('A-B', '<A', '>A' или пустая). Бери только числовые/качественные "
    "лабораторные показатели. НЕ включай ФИО, врача, комментарии, интерпретации."
)


def is_enabled() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _extract_json(text: str) -> dict:
    """Достаёт первый JSON-объект из ответа модели (толерантно к обёрткам)."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text).rstrip("`").strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        return {}
    try:
        return json.loads(text[start:end + 1])
    except ValueError:
        return {}


def extract(text: str, *, timeout: float = 60.0) -> dict:
    """Вызывает Claude API. Возвращает {date, params[]}. При любой ошибке — пустой результат."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return {"date": None, "params": []}
    model = os.environ.get("ANTHROPIC_MODEL", DEFAULT_MODEL)
    # ограничим объём — лаб-PDF небольшие; отрежем на всякий случай
    payload = {
        "model": model,
        "max_tokens": 4096,
        "system": _SYS,
        "messages": [{"role": "user", "content": text[:60000]}],
    }
    headers = {
        "x-api-key": key,
        "anthropic-version": API_VERSION,
        "content-type": "application/json",
    }
    try:
        r = httpx.post(API_URL, headers=headers, json=payload, timeout=timeout)
        r.raise_for_status()
        data = r.json()
        parts = data.get("content") or []
        out_text = "".join(p.get("text", "") for p in parts if p.get("type") == "text")
    except Exception:  # noqa: BLE001 — fallback никогда не должен ронять ingest
        return {"date": None, "params": []}

    obj = _extract_json(out_text)
    date = obj.get("date")
    if date and not re.match(r"^\d{4}-\d{2}-\d{2}$", str(date)):
        date = None
    params = []
    for p in obj.get("params", []) or []:
        if not isinstance(p, dict):
            continue
        name = (p.get("name") or "").strip()
        val = p.get("value")
        if not name or val is None or str(val).strip() == "":
            continue
        params.append({
            "name": name,
            "raw_name": name,
            "value": str(val).strip(),
            "unit": (p.get("unit") or "").strip(),
            "ref": (p.get("ref") or "").strip(),
        })
    return {"date": date, "params": params}
