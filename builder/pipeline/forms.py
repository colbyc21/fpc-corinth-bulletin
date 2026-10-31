"""The volunteer form ↔ week.json.

``form_to_week`` takes the posted fields (a flat mapping of name → value,
with repeated names as lists) and returns a new week dict with the content
parts replaced. The liturgy items and notes are not on the form: they come
from the parse step and are revised through the chat box or the JSON editor.
Everything is validated by ``week.save_week`` afterwards.
"""
from __future__ import annotations

import re
from copy import deepcopy

from builder.pipeline import dates
from builder.pipeline.store import ROLE_LABELS

SERVE_KEYS = list(ROLE_LABELS)
FLOWER_KEYS = ("given", "message", "quote", "reference", "closing", "signed")
INSERTS = {"thanksgiving-meal": "Thanksgiving Meal volunteer insert"}
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MD = re.compile(r"^(?P<m>[A-Za-z]{3,9})\.?\s+(?P<d>\d{1,2})$")


def _s(form: dict, key: str) -> str:
    v = form.get(key, "")
    if isinstance(v, list):
        v = v[0] if v else ""
    return (v or "").strip()


def _list(form: dict, key: str) -> list[str]:
    v = form.get(key, [])
    if not isinstance(v, list):
        v = [v]
    return [(x or "").strip() for x in v]


def parse_calendar_lines(text: str, sunday: str) -> list[dict]:
    """One line per date: ``2026-11-03: Messy Bible Study, 8 a.m. · Election Day``.
    A bare ``Nov 3`` date is read in the Sunday's year (next year if it falls before the Sunday in December)."""
    out = []
    year = int(sunday[:4])
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if ":" not in line:
            raise ValueError(f"calendar line needs 'date: items' — {line!r}")
        d, items = line.split(":", 1)
        d = d.strip()
        if not _ISO.match(d):
            m = _MD.match(d)
            if not m:
                raise ValueError(f"calendar date not understood: {d!r}")
            try:
                month = dates.month_number(m.group("m"))
            except Exception:
                raise ValueError(f"calendar month not understood: {d!r}") from None
            y = year + 1 if (month == 1 and sunday[5:7] == "12") else year
            d = f"{y}-{month:02d}-{int(m.group('d')):02d}"
        parts = [p.strip() for p in re.split(r"\s+[·|]\s+|;\s*", items) if p.strip()]
        if not parts:
            raise ValueError(f"calendar line {d} has no items")
        out.append({"date": d, "items": parts})
    return out


def calendar_lines_text(calendar: list[dict]) -> str:
    return "\n".join(f"{c['date']}: {' · '.join(c['items'])}" for c in calendar)


def form_to_week(week: dict, form: dict) -> dict:
    w = deepcopy(week)
    w["subtitle_override"] = _s(form, "subtitle_override") or None
    w["special"] = {"communion": _s(form, "communion") == "on", "canvass": _s(form, "canvass") == "on"}
    w["welcome_variant"] = _s(form, "welcome_variant") or "regular"
    if w["welcome_variant"] == "custom":
        paras = [p.strip() for p in _s(form, "welcome_custom").split("\n\n") if p.strip()]
        w["welcome_custom"] = paras or None
    else:
        w["welcome_custom"] = None
    page = _s(form, "pew_bible_page")
    if page and not page.isdigit():
        raise ValueError("Pew Bible page must be a number")
    w["pew_bible_page"] = int(page) if page else None

    s = w.setdefault("sermon", {})
    s["title"] = _s(form, "sermon_title")
    s["scripture"] = _s(form, "sermon_scripture")
    s["preacher"] = _s(form, "sermon_preacher") or "Rev. Waring Porter"
    if not s["title"] or not s["scripture"]:
        raise ValueError("Sermon title and scripture are required")

    given = _s(form, "flowers_given")
    if given:
        fl = {"given": given}
        for k in FLOWER_KEYS[1:]:
            fl[k] = _s(form, f"flowers_{k}") or None
        w["flowers"] = fl
    else:
        w["flowers"] = None

    titles, bodies = _list(form, "ann_title"), _list(form, "ann_body")
    suffixes, details = _list(form, "ann_print_suffix"), _list(form, "ann_detail")
    anns = []
    for i, (t, b) in enumerate(zip(titles, bodies)):
        if not t and not b:
            continue
        if not t or not b:
            raise ValueError(f"announcement {i + 1} needs both a title and a body")
        a = {"title": t, "body": " ".join(b.split())}
        if i < len(suffixes) and suffixes[i]:
            a["print_suffix"] = suffixes[i]
        if i < len(details) and details[i]:
            a["detail"] = details[i]
        anns.append(a)
    if len(anns) != len(w.get("announcements", [])):
        w.setdefault("layout", {})["announcement_columns"] = None
    w["announcements"] = anns

    w["serve"] = {k: v for k in SERVE_KEYS if (v := _s(form, f"serve_{k}"))}
    w["calendar"] = parse_calendar_lines(_s(form, "calendar_lines"), w["date"])
    w["inserts"] = [k for k in INSERTS if _s(form, f"insert_{k}") == "on"]
    return w
