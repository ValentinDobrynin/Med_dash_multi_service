# -*- coding: utf-8 -*-
"""Регрессия парсер-фич, портированных из p2 (копрограмма, CBC-синонимы,
orphan-референсы ВЭЖХ-МС). Без сети/сессий — прямые вызовы lab_engine.

Держит четвёртую копию парсера синхронной с движком/осн.сервисом/p2 (§3.4 плана):
если копрограмма снова «отвалится» при форке — падает здесь, а не у юзера.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def test_coprogram_deviations_only():
    """Копрограмма ДНКОМ: только отклонения (результат≠норма) + pH; норма отбрасывается."""
    from lab_engine.parse_core import parse_coprogram
    txt = ("КОПРОГРАММА\n"
           "Цвет                     коричневый          коричневый\n"       # норма -> skip
           "pH                       6,5                 6-8\n"
           "Жирные кислоты           мало                отсутствуют\n"      # отклонение
           "Крахмал внеклеточный     отсутствует         отсутствует\n"      # норма -> skip
           "Комментарии: Обнаружены оксалаты.\n")
    out = parse_coprogram(txt)
    names = {p["name"] for p in out}
    assert "Жирные кислоты (кал)" in names
    assert "Кислотность кала" in names
    assert "Оксалаты (кал)" in names
    assert "Цвет кала" not in names          # норма отброшена
    assert all(p.get("group") == "coprogram" for p in out)


def test_coprogram_in_full_preview():
    """Копрограмма проходит через build-путь lab_ingest.parse_text_to_rows и
    попадает в canonical rows под panel=coprogram (не режется блок-листом)."""
    from lab_ingest import parse_text_to_rows
    txt = ("КОПРОГРАММА\n"
           "Взятие биоматериала: 05.06.2026\n"
           "pH                       6,5                 6-8\n"
           "Жирные кислоты           мало                отсутствуют\n")
    _visits, rows, _rejects, _dates = parse_text_to_rows(txt, "coprogram.pdf")
    panels = {r["panel"] for r in rows}
    assert "coprogram" in panels, f"копрограмма не распозналась: {rows}"


def test_cbc_synonyms():
    from lab_engine.parse_core import normalize_key
    assert normalize_key("Сегментоядерные") == "Нейтрофилы"
    assert normalize_key("Средний объем тромбоцита") == "MPV"


def test_orphan_ref_merge():
    from lab_engine.parse_core import parse_text
    txt = ("Витамин B1 (тиамин)        2,55        нг/мл\n"
           "                                          2,1                4,3\n")
    ps = parse_text(txt)
    b1 = [p for p in ps if p["name"] == "Витамин B1"]
    assert b1 and b1[0]["ref"].replace(" ", "") in ("2,1-4,3", "2.1-4.3")
