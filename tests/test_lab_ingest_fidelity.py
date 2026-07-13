# -*- coding: utf-8 -*-
"""Э1 acceptance-gate: серверный порт парсера должен давать ТЕ ЖЕ canonical rows,
что и локальный движок (emit_ndjson) на реальных кешированных .txt.

Тест обходит pdftotext (в песочнице poppler может отсутствовать): подаёт текст
из .txt_cache напрямую в parse_text_to_rows и сравнивает с ground truth от
healthcare/scripts/emit_ndjson.emit(--files).

Скипается, если healthcare-репо не примонтирован рядом.
"""
import sys
from pathlib import Path

import pytest

SERVICE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVICE))

# healthcare репо (ground truth). На Render его нет — тест только для локальной сверки.
HC = Path("/sessions/jolly-busy-wozniak/mnt/healthcare")
TXT = HC / ".txt_cache"

pytestmark = pytest.mark.skipif(
    not (HC / "scripts" / "emit_ndjson.py").exists() or not TXT.exists(),
    reason="healthcare репо не примонтирован — сверка с локальным движком недоступна",
)

# Набор реальных лаб-файлов, покрывающих форматы: EMC, Lansergof, DNKOM(line),
# DNKOM(multi-line антитела), гормоны/слюна, свежий пакет 2026 DNKOM.
FILES = [
    "2016-09-25 EMC blood general",
    "2017-08-09 Lansergof blood tests",
    "2018-07-26 DNKOM biochem blood test",
    "2018-07-26 DNKOM general blood test",
    "2018-07-26 DNKOM vitamins",
    "2019-03-29 DNKOM hormones",
    "2026-05-18 ДНКОМ - биохимия",
    "2026-05-18 ДНКОМ - витамины и микроэлементы",
    "2026-05-18 ДНКОМ - ОАК, ретикулоциты",
]

KEY = ("analyte_id", "sample_date", "seq", "value_num", "value_text",
       "unit", "ref_low", "ref_high", "source")


def _norm(row: dict) -> tuple:
    return tuple(row.get(k) for k in KEY)


def _ground_truth(file_sub: str):
    import json
    hc_scripts = HC / "scripts"
    sys.path.insert(0, str(hc_scripts))
    import importlib
    emit_mod = importlib.import_module("emit_ndjson")
    lines, _rejects = emit_mod.emit(lambda r: file_sub in (r.get("file") or ""))
    return {_norm(json.loads(l)) for l in lines}


def _port_rows(file_sub: str):
    from lab_ingest import parse_text_to_rows
    txt_path = TXT / (file_sub + ".txt")
    text = txt_path.read_text(encoding="utf-8", errors="ignore")
    _visits, rows, _rejects, _dates = parse_text_to_rows(text, file_sub)
    return {_norm(r) for r in rows}


@pytest.mark.parametrize("file_sub", FILES)
def test_port_matches_local_engine(file_sub):
    truth = _ground_truth(file_sub)
    port = _port_rows(file_sub)
    missing = truth - port      # движок нашёл, порт потерял
    extra = port - truth        # порт добавил лишнее
    assert not missing and not extra, (
        f"\n{file_sub}:\n"
        f"  потеряно портом ({len(missing)}): {sorted(missing)[:8]}\n"
        f"  лишнее в порте ({len(extra)}): {sorted(extra)[:8]}"
    )


def test_files_present():
    absent = [f for f in FILES if not (TXT / (f + ".txt")).exists()]
    assert not absent, f"нет кеша для: {absent}"
