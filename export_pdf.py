"""Сборка PDF-отчёта по анализам для отправки в Telegram — в тёмной теме дэша.

Порядок (по требованию): сначала все ГРАФИКИ (по числовым маркерам), затем все
ТАБЛИЦЫ. Для одного маркера — его график + таблица. Кириллица — штатный DejaVu Sans.

Публичная функция: build_report_pdf(items) -> bytes.
  items: список dict:
    {name_ru, unit, direction, value_type, ref_low, ref_high,
     points: [{date, value_num, value_text, seq, ref_raw}]}  # points по возрастанию даты
"""
from __future__ import annotations
import io
from datetime import datetime

import matplotlib
matplotlib.use("Agg")  # без дисплея
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.ticker import FuncFormatter

# ── Палитра дэша (тёмная индиго) ──
BG      = "#0a0a1a"   # фон страницы
PANEL   = "#12122a"   # фон панели/строк
INK     = "#e7e7f2"   # основной текст
MUTED   = "#9aa0b4"   # приглушённый
ACCENT  = "#818cf8"   # индиго — линия значений
GRID    = "#26264a"
OK      = "#34d399"
WARN    = "#fbbf24"
ALERT   = "#f87171"
HEAD_BG = "#1f2440"   # шапка таблицы

_Y2 = FuncFormatter(lambda v, _pos: f"{v:.2f}".rstrip("0").rstrip("."))  # ≤2 знака


def _fmt(v):
    return f"{v:.2f}".rstrip("0").rstrip(".")


def _status(direction, value, lo, hi):
    if value is None or direction == "informational":
        return ""
    if direction == "higher_worse":
        return "вне нормы" if (hi is not None and value > hi) else "ок"
    if direction == "lower_worse":
        return "вне нормы" if (lo is not None and value < lo) else "ок"
    if direction == "window":
        bad = (lo is not None and value < lo) or (hi is not None and value > hi)
        return "вне нормы" if bad else "ок"
    return ""


def _style_ax(ax):
    ax.set_facecolor(BG)
    for sp in ax.spines.values():
        sp.set_color(GRID)
    ax.tick_params(colors=MUTED, labelsize=9)
    ax.title.set_color(INK)


def _save(pdf, fig):
    fig.patch.set_facecolor(BG)
    fig.tight_layout()
    pdf.savefig(fig, facecolor=BG)
    plt.close(fig)


def _numeric_points(item):
    return [p for p in item["points"] if p.get("value_num") is not None]


def _plot_marker(pdf, item):
    pts = _numeric_points(item)
    if not pts:
        return False
    xs = [datetime.strptime(p["date"], "%Y-%m-%d") for p in pts]
    ys = [p["value_num"] for p in pts]
    fig, ax = plt.subplots(figsize=(8.0, 3.4))
    _style_ax(ax)
    lo, hi = item.get("ref_low"), item.get("ref_high")
    if lo is not None and hi is not None:
        ax.axhspan(lo, hi, color=OK, alpha=0.10)
    elif hi is not None:
        ax.axhline(hi, color=OK, ls="--", lw=1, alpha=0.6)
    elif lo is not None:
        ax.axhline(lo, color=OK, ls="--", lw=1, alpha=0.6)
    ax.plot(xs, ys, marker="o", ms=4.5, lw=1.8, color=ACCENT,
            markerfacecolor=ACCENT, markeredgecolor=BG, markeredgewidth=1.2)
    unit = item.get("unit") or ""
    ax.set_title(f"{item['name_ru']}" + (f" ({unit})" if unit else ""), fontsize=12.5, fontweight="bold")
    ax.yaxis.set_major_formatter(_Y2)
    ax.grid(True, ls=":", lw=0.5, color=GRID, alpha=0.6)
    fig.autofmt_xdate(rotation=30)
    _save(pdf, fig)
    return True


def _table_marker(pdf, item):
    pts = item["points"]
    if not pts:
        return
    lo, hi = item.get("ref_low"), item.get("ref_high")
    ref_raw = None
    for p in reversed(pts):
        if p.get("ref_raw"):
            ref_raw = p["ref_raw"]
            break
    rows, statuses = [], []
    for p in pts:
        if p.get("value_num") is not None:
            val = _fmt(p["value_num"])
            st = _status(item.get("direction"), p["value_num"], lo, hi)
        else:
            val = p.get("value_text") or "—"
            st = ""
        rows.append([p["date"], val, item.get("unit") or "", ref_raw or "", st])
        statuses.append(st)

    fig_h = min(0.7 + 0.30 * (len(rows) + 1), 10.0)
    fig, ax = plt.subplots(figsize=(8.0, fig_h))
    ax.set_facecolor(BG)
    ax.axis("off")
    ax.set_title(item["name_ru"], fontsize=12.5, fontweight="bold", loc="left", pad=12, color=INK)
    tbl = ax.table(
        cellText=rows,
        colLabels=["Дата", "Значение", "Ед.", "Референс", "Статус"],
        colWidths=[0.20, 0.20, 0.15, 0.28, 0.17],
        loc="upper center",
        cellLoc="left",
    )
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(9.5)
    tbl.scale(1, 1.4)
    for (r, c), cell in tbl.get_celld().items():
        cell.set_edgecolor(GRID)
        cell.set_linewidth(0.6)
        if r == 0:
            cell.set_facecolor(HEAD_BG)
            cell.set_text_props(color=INK, fontweight="bold")
        else:
            cell.set_facecolor(PANEL if r % 2 else BG)
            st = statuses[r - 1]
            if c == 4 and st == "вне нормы":
                cell.set_text_props(color=ALERT, fontweight="bold")
            elif c == 4 and st == "ок":
                cell.set_text_props(color=OK)
            else:
                cell.set_text_props(color=INK)
    _save(pdf, fig)


def build_report_pdf(items) -> bytes:
    buf = io.BytesIO()
    with PdfPages(buf) as pdf:
        for it in items:
            _plot_marker(pdf, it)
        for it in items:
            _table_marker(pdf, it)
        if not items:
            fig, ax = plt.subplots(figsize=(8, 2))
            _style_ax(ax); ax.axis("off")
            ax.text(0.5, 0.5, "Нет данных", ha="center", va="center", color=INK)
            _save(pdf, fig)
    return buf.getvalue()
