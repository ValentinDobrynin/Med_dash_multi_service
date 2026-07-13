"""Серверный порт лабораторного движка (ТЗ №4).

Компоненты, vendored из healthcare/scripts (максимальная фиделити):
  parse_core.py   — verbatim parse_healthcare.py (regex-парсер, SYNONYMS, даты, split_visits)
  blocklist.py    — pure is_blocked_name / norm_unit / categorize из build_master.py
  canonicalize.py — порт emit_ndjson.emit() (in-memory, без parsed.json)
  analyte_dictionary.yaml — verbatim словарь C1 (alias -> analyte_id + meta)
Оркестратор ingest — service/lab_ingest.py.
"""
