# -*- coding: utf-8 -*-
"""Каноникализация распознанных параметров -> NDJSON-строки (контракт C2).

Порт логики healthcare/scripts/emit_ndjson.py::emit(), но принимает данные
В ПАМЯТИ (список visit-записей формата parsed.json), а не читает parsed.json.
Резолв имён — только через analyte_dictionary.yaml; незамапленное немусорное имя
уходит в reject (не тихий дроп). Единица/seq/референс — 1:1 с движком.

Публичное:  canonicalize(visits) -> (rows, rejects)
  visits: [{"date","lab","params":[{"name","raw_name","value","unit","ref","group"}...]}]
  rows:   [dict в схеме lab_results ingest]
  rejects:[{"name","date","reason"}]
"""
from __future__ import annotations
import re
from pathlib import Path
from collections import defaultdict, Counter

import yaml

from .blocklist import is_blocked_name, norm_unit, categorize

DICT_PATH = Path(__file__).resolve().parent / "analyte_dictionary.yaml"
SOURCE_LAB_SKIP = {"Скан (внешний документ)"}

_DICT_CACHE = None


def norm_alias(s: str) -> str:
    """Нормализация имени для сопоставления: lower, без пробелов/дефисов/скобок."""
    s = s.lower().strip()
    s = re.sub(r"[\s\-\(\)\.,/]", "", s)
    return s


def load_dictionary(path: Path = DICT_PATH):
    """(alias2id, meta). Кэшируется — словарь иммутабелен в рамках процесса."""
    global _DICT_CACHE
    if _DICT_CACHE is not None:
        return _DICT_CACHE
    entries = yaml.safe_load(path.read_text(encoding="utf-8"))
    alias2id = {}
    meta = {}
    for e in entries:
        aid = e["analyte_id"]
        meta[aid] = e
        cands = set(e.get("aliases") or [])
        cands.add(e["name_ru"])
        for a in cands:
            na = norm_alias(a)
            if na and na not in alias2id:
                alias2id[na] = aid
    _DICT_CACHE = (alias2id, meta)
    return _DICT_CACHE


def parse_ref(ref):
    """(low, high, ref_raw). Толерантен к %, единицам, запятым, 0-0."""
    if ref is None:
        return None, None, None
    raw = str(ref).strip()
    if not raw:
        return None, None, None
    s = raw.replace(",", ".")
    s = re.sub(r"[%‰]", "", s)
    m = re.search(r"(-?\d+(?:\.\d+)?)\s*[-–—]\s*(-?\d+(?:\.\d+)?)", s)
    if m:
        lo, hi = float(m.group(1)), float(m.group(2))
        if lo == 0.0 and hi == 0.0:
            return None, None, raw
        return lo, hi, raw
    m = re.search(r"[<≤]\s*(-?\d+(?:\.\d+)?)", s)
    if m:
        return None, float(m.group(1)), raw
    m = re.search(r"[>≥]\s*(-?\d+(?:\.\d+)?)", s)
    if m:
        return float(m.group(1)), None, raw
    m = re.fullmatch(r"\s*(-?\d+(?:\.\d+)?)\s*", s)
    if m:
        return None, float(m.group(1)), raw
    return None, None, raw


_NUM_RE = re.compile(r"^[<>≤≥]?\s*(-?\d+(?:\.\d+)?)")


def to_num(v):
    s = str(v).replace(",", ".").strip()
    m = _NUM_RE.match(s)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def canonicalize(visits, dict_path: Path = DICT_PATH):
    """visits -> (rows, rejects). 1:1 с emit_ndjson.emit(), но in-memory."""
    alias2id, meta = load_dictionary(dict_path)

    raw_rows = []
    rejects = []
    unit_counter = defaultdict(Counter)

    for r in visits:
        date = r.get("date")
        if not date:
            continue
        if r.get("lab") in SOURCE_LAB_SKIP:
            continue
        source = r.get("lab") or "Неизвестно"
        for p in r.get("params", []):
            name = p.get("name", "")
            group = p.get("group", "")
            # Копрограмма (group='coprogram') — намеренные качественные маркеры,
            # блок-лист (жиры/клетчатка/крахмал…) для них НЕ применяем.
            if group != "coprogram" and is_blocked_name(name):
                continue
            if categorize(name, group) == "antibiotic":
                continue
            aid = alias2id.get(norm_alias(name)) or alias2id.get(norm_alias(p.get("raw_name", "")))
            if not aid:
                rejects.append({"name": name, "date": date, "reason": "unmapped analyte name"})
                continue
            u = p.get("unit") or ""
            nu = norm_unit(u)
            if nu:
                unit_counter[aid][nu] += 1
            raw_rows.append({
                "aid": aid, "date": date, "value": p.get("value"),
                "unit": u, "nu": nu, "ref": p.get("ref"), "source": source,
            })

    dominant = {aid: c.most_common(1)[0][0] for aid, c in unit_counter.items()}

    grouped = defaultdict(list)
    for row in raw_rows:
        grouped[(row["aid"], row["date"])].append(row)
    kept = {}
    for key, rows in grouped.items():
        aid = key[0]
        dom = dominant.get(aid)
        if dom:
            preferred = [r for r in rows if r["nu"] == dom]
            kept[key] = preferred if preferred else rows[:1]
        else:
            kept[key] = rows

    out_rows = []
    for (aid, date), rows in sorted(kept.items()):
        m = meta[aid]
        vtype = m["value_type"]
        unit_canon = m.get("unit_canonical")
        canon_rows = []
        seen_sig = set()
        for row in rows:
            ref_low, ref_high, ref_raw = parse_ref(row["ref"])
            value_num = None
            value_text = None
            if vtype in ("qualitative", "titer"):
                val = row["value"]
                num = to_num(val)
                if vtype == "titer" and num is not None:
                    value_num = num
                    value_text = None
                else:
                    value_text = None if val is None else str(val).strip()
            else:
                num = to_num(row["value"])
                if num is None:
                    value_text = None if row["value"] is None else str(row["value"]).strip()
                else:
                    value_num = num
            sig = (value_num, value_text, ref_low, ref_high)
            if sig in seen_sig:
                continue
            seen_sig.add(sig)
            canon_rows.append((value_num, value_text, ref_low, ref_high, ref_raw, row))
        for seq, (value_num, value_text, ref_low, ref_high, ref_raw, row) in enumerate(canon_rows):
            out_rows.append({
                "analyte_id": aid,
                "panel": m["panel"],
                "sample_date": date,
                "seq": seq,
                "value_num": value_num,
                "value_text": value_text,
                "unit": unit_canon or (row["unit"] or None),
                "ref_low": ref_low,
                "ref_high": ref_high,
                "ref_raw": ref_raw,
                "source": row["source"],
                "name_ru": m["name_ru"],
            })

    return out_rows, rejects
