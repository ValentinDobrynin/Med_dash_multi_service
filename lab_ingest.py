# -*- coding: utf-8 -*-
"""Оркестратор серверного разбора лабораторных PDF (ТЗ №4, Э1 — dry-run).

pdf_bytes -> pdftotext -layout -> текст -> split_visits -> parse_text (движок) ->
canonicalize -> ПРЕВЬЮ. Ничего не пишет в БД (это делает Э2 confirm).

Публичное:
  extract_text_from_pdf(pdf_bytes) -> str          (poppler; пусто => скан)
  parse_text_to_rows(text, filename) -> dict        (визиты -> canonical rows)
  build_preview(pdf_bytes, filename) -> dict        (полный путь: reject/ok + rows)

Формат preview:
  {
    "ok": bool,
    "reason": str|None,          # если not ok: 'scan_no_text' | 'no_date' | 'no_values'
    "dates": [...],
    "row_count": int,
    "rows": [...canonical rows...],
    "rejects": [{name,date,reason}...],
    "summary": "Распознал N значений за <дата(ы)>: маркер1, маркер2, …",
  }
"""
from __future__ import annotations
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from lab_engine.parse_core import (
    detect_lab, extract_date, split_visits, parse_text, parse_coprogram,
)
from lab_engine.canonicalize import canonicalize

# Порог «базовый парсер справился» — иначе кандидат на fallback (Э3).
MIN_VALUES_OK = 3


class PopplerMissing(RuntimeError):
    pass


def _pdftotext_bin() -> str | None:
    return shutil.which("pdftotext")


def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    """pdftotext -layout. Пустой результат => скан без текстового слоя."""
    binp = _pdftotext_bin()
    if not binp:
        raise PopplerMissing("pdftotext (poppler-utils) не установлен на сервере")
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=True) as tf:
        tf.write(pdf_bytes)
        tf.flush()
        try:
            out = subprocess.run(
                [binp, "-layout", "-enc", "UTF-8", tf.name, "-"],
                capture_output=True, timeout=60,
            )
        except subprocess.TimeoutExpired:
            return ""
    if out.returncode != 0:
        return ""
    return out.stdout.decode("utf-8", errors="ignore")


def _apply_saliva_fix(params):
    """Контекстная корректировка тестостерона в слюне — как в parse_healthcare.main()."""
    for p in params:
        if p.get("name") != "Тестостерон общий":
            continue
        low_ref = re.match(r"\s*>\s*(\d+(?:[.,]\d+)?)\s*$", p.get("ref", "") or "")
        ref_low_val = float(low_ref.group(1).replace(",", ".")) if low_ref else None
        try:
            val_num = float(str(p["value"]).replace(",", "."))
        except (ValueError, TypeError):
            val_num = None
        unit = p.get("unit", "") or ""
        if (ref_low_val is not None and ref_low_val < 5) or (
            val_num is not None and val_num < 3 and ("нмоль" in unit or "nmol" in unit)
        ):
            p["name"] = "Тестостерон в слюне"


def parse_text_to_rows(text: str, filename: str = "upload.pdf"):
    """Текст (pdftotext -layout) -> (visits, rows, rejects, dates).

    Спец-парсеры (спермограмма/InBody) НЕ подключены — вне области ТЗ №4.
    """
    lab = detect_lab(filename, text)
    is_saliva = "слюн" in filename.lower() or "слюн" in text.lower()[:500]
    visits = []
    for visit_date_chunk, chunk_text in split_visits(text):
        date = visit_date_chunk or extract_date(filename, text)
        params = parse_text(chunk_text)
        params += parse_coprogram(chunk_text)   # копрограмма: отклонения + pH (qual)
        if is_saliva:
            for p in params:
                if p.get("name") == "Тестостерон общий":
                    p["name"] = "Тестостерон в слюне"
        _apply_saliva_fix(params)
        visits.append({"file": filename, "date": date, "lab": lab, "params": params})
    rows, rejects = canonicalize(visits)
    dates = sorted({v["date"] for v in visits if v["date"]})
    return visits, rows, rejects, dates


def _summary(rows, dates, rejects) -> str:
    if not rows:
        return "Не распознал ни одного значения."
    names = []
    seen = set()
    for r in rows:
        n = r.get("name_ru") or r["analyte_id"]
        if n not in seen:
            seen.add(n)
            names.append(n)
    dpart = ", ".join(dates) if dates else "без даты"
    head = f"Распознал {len(rows)} значений за {dpart}: " + ", ".join(names[:25])
    if len(names) > 25:
        head += f" … (+{len(names) - 25})"
    if rejects:
        rj = sorted({r["name"] for r in rejects})
        head += f"\nНе распознал (в reject): {', '.join(rj[:15])}"
    return head


def _fallback_rows(text: str, filename: str):
    """ТЗ №4 Э3: Claude API извлекает параметры, затем общий canonicalize.

    Возвращает (rows, rejects, dates) либо (None, ...) если fallback выключен/пуст.
    """
    try:
        import lab_fallback
    except ImportError:
        return None, [], []
    if not lab_fallback.is_enabled():
        return None, [], []
    res = lab_fallback.extract(text)
    params = res.get("params") or []
    if not params:
        return None, [], []
    date = res.get("date") or extract_date(filename, text)
    lab = detect_lab(filename, text)
    visits = [{"file": filename, "date": date, "lab": lab, "params": params}]
    from lab_engine.canonicalize import canonicalize as _canon
    rows, rejects = _canon(visits)
    dates = sorted({v["date"] for v in visits if v["date"]})
    return rows, rejects, dates


def build_preview(pdf_bytes: bytes, filename: str = "upload.pdf") -> dict:
    """Полный путь: PDF -> текст -> парс -> canonical rows -> preview. Ничего не пишет.

    Если базовый парсер слаб (нет даты / < MIN_VALUES_OK) — пробует Claude-fallback
    (только при заданном ANTHROPIC_API_KEY). Источник в превью помечается 'fallback'.
    """
    text = extract_text_from_pdf(pdf_bytes)
    if not text or len(text.strip()) < 20:
        return {
            "ok": False, "reason": "scan_no_text",
            "dates": [], "row_count": 0, "rows": [], "rejects": [],
            "summary": ("Похоже, это скан без текстового слоя. Поддерживаются только "
                        "PDF-анализы с текстовым слоем — обработай такой файл на компьютере."),
        }
    visits, rows, rejects, dates = parse_text_to_rows(text, filename)

    used_fallback = False
    if not dates or len(rows) < MIN_VALUES_OK:
        fb_rows, fb_rejects, fb_dates = _fallback_rows(text, filename)
        if fb_rows and len(fb_rows) > len(rows):
            rows, rejects, dates = fb_rows, fb_rejects, fb_dates
            used_fallback = True
    if not dates:
        return {
            "ok": False, "reason": "no_date",
            "dates": [], "row_count": len(rows), "rows": rows, "rejects": rejects,
            "summary": "Не смог определить дату забора — заливка отменена.",
        }
    if len(rows) < 1:
        return {
            "ok": False, "reason": "no_values",
            "dates": dates, "row_count": 0, "rows": [], "rejects": rejects,
            "summary": ("Не распознал лабораторных значений. Возможно, это не табличный "
                        "анализ (выписка/заключение) — такие поддерживаются только с компьютера."),
        }
    summary = _summary(rows, dates, rejects)
    if used_fallback:
        summary = "🤖 (распознано через Claude-fallback)\n" + summary
    return {
        "ok": True, "reason": None,
        "dates": dates, "row_count": len(rows), "rows": rows, "rejects": rejects,
        "weak": len(rows) < MIN_VALUES_OK,
        "used_fallback": used_fallback,
        "summary": summary,
    }
