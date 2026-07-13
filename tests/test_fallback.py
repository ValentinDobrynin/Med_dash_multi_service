# -*- coding: utf-8 -*-
"""Э3 acceptance: Claude-fallback вызывается только когда базовый парсер слаб,
и не вызывается на нормальном PDF. Реального API-вызова нет (мок)."""
import shutil
import sys
from pathlib import Path

import pytest

SERVICE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVICE))

HC = Path("/sessions/jolly-busy-wozniak/mnt/healthcare")
PDF = HC / "archive" / "2022-01-12 ДОБРЫНИН В С - 3301434451 (Биохимический анализ крови).pdf"

pytestmark = pytest.mark.skipif(
    not shutil.which("pdftotext") or not PDF.exists(),
    reason="нужен pdftotext + healthcare-репо",
)


def test_fallback_not_called_on_strong_pdf(monkeypatch):
    import lab_fallback
    called = {"n": 0}
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")  # включён, но не должен зваться

    def spy(text, **kw):
        called["n"] += 1
        return {"date": None, "params": []}

    monkeypatch.setattr(lab_fallback, "extract", spy)
    import importlib
    import lab_ingest
    importlib.reload(lab_ingest)
    pv = lab_ingest.build_preview(PDF.read_bytes(), "biochem.pdf")
    assert pv["ok"] and pv["row_count"] >= 10
    assert not pv.get("used_fallback")
    assert called["n"] == 0  # 24 значения >> MIN — fallback не нужен


def test_fallback_used_when_base_empty(monkeypatch):
    """Симулируем «сложный» PDF: базовый парс пуст -> fallback отдаёт значения."""
    import lab_fallback
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setattr(lab_fallback, "extract", lambda text, **kw: {
        "date": "2026-06-01",
        "params": [
            {"name": "Гемоглобин", "raw_name": "Гемоглобин", "value": "150", "unit": "г/л", "ref": "130-170"},
            {"name": "Глюкоза", "raw_name": "Глюкоза", "value": "5.1", "unit": "ммоль/л", "ref": "4.1-5.9"},
            {"name": "Креатинин", "raw_name": "Креатинин", "value": "90", "unit": "мкмоль/л", "ref": "62-106"},
            {"name": "АЛТ", "raw_name": "АЛТ", "value": "30", "unit": "Ед/л", "ref": "0-41"},
        ],
    })
    import importlib
    import lab_ingest
    importlib.reload(lab_ingest)
    # base parser gets almost nothing from this prose; fallback fills in
    prose = ("Заключение врача. Пациент осмотрен. Рекомендована диета. "
             "Никаких табличных значений здесь нет, только текст на пару абзацев. " * 3)
    monkeypatch.setattr(lab_ingest, "extract_text_from_pdf", lambda b: prose)
    pv = lab_ingest.build_preview(b"%PDF-fake", "hard.pdf")
    assert pv["ok"] and pv.get("used_fallback") is True
    assert pv["row_count"] == 4
    assert "Claude-fallback" in pv["summary"]


def test_fallback_disabled_without_key(monkeypatch):
    import lab_fallback
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert lab_fallback.is_enabled() is False
    assert lab_fallback.extract("любой текст")["params"] == []
