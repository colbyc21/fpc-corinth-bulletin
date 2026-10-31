"""Intake: the week's input files → week.json, via the two parse calls.

``intake(date, liturgy=..., sermon=..., content=..., announcements=...)``
loads or scaffolds the week, runs the liturgy parse and the content copyedit
on whatever files were given, merges the results, applies the deterministic
rules (liturgical day, welcome variant, inserts) and saves. Files that are
not given leave their part of the week untouched, so the office can submit
the liturgy on Thursday and the content form on Friday.
"""
from __future__ import annotations

import json
from pathlib import Path

from builder.pipeline import calendar_feed, content as contentmod, dates, extract, model, parse as parsemod
from builder.pipeline import week as weekmod
from builder.pipeline.store import Store

KNOWN_INSERTS = {"thanksgiving-meal": ("thanksgiving meal", "community thanksgiving")}


def _welcome_variant(items: list[dict]) -> str:
    titles = " ".join(it["title"].lower() for it in items)
    return "regular" if "children" in titles and "dismissed" in titles else "school_break"


def _inserts(announcements: list[dict], announcements_text: str | None) -> list[str]:
    text = (announcements_text or "").lower() + " ".join((a.get("body", "") + a.get("print_suffix", "")).lower() for a in announcements)
    out = []
    for name, keys in KNOWN_INSERTS.items():
        if any(k in text for k in keys) and "insert" in text:
            out.append(name)
    return out


def intake(date: str, *, store: Store, liturgy: Path | None = None, sermon: Path | None = None,
           content: Path | None = None, announcements: Path | None = None, example: str | None = "2026-11-01",
           user: str | None = None) -> dict:
    """Returns {week, flags, changes, jobs, cost}. Raises model.ModelError when a call cannot run."""
    path = weekmod.week_path(date)
    if path.exists():
        week = weekmod.load_week(date)
    else:
        week = weekmod.scaffold(date, serve=store.serve_for(date), calendar=calendar_feed.week_calendar(store, date))
    flags: list[dict] = []
    changes: list[dict] = []
    jobs: list[dict] = []
    ann_text = extract.rtf_text(announcements) if announcements else None

    if liturgy:
        lit_text = extract.docx_text(liturgy)
        ser_text = extract.docx_text(sermon) if sermon else None
        if not ser_text:
            # a SERMON OUTLINE block in the liturgy docx stands in for the notes
            pass
        raw = parsemod.parse_documents(date, lit_text, ser_text, store=store, example=example)
        jobs.append(raw.pop("_job"))
        res = parsemod.postprocess(date, raw, store)
        week["liturgy"] = res["liturgy"]
        week["sermon"] = {**week.get("sermon", {}), **res["sermon"]}
        week["special"] = res["special"]
        week["pew_bible_page"] = res["pew_bible_page"]
        week["welcome_variant"] = _welcome_variant(res["liturgy"]["items"])
        week["layout"]["pages"] = None   # repaginate from the new items
        week["layout"]["notes"] = []
        week["layout"]["page_style"] = {}
        week["layout"]["overrides"] = []
        flags += res["flags"]
        changes += res["changes"]

    if content:
        c_text = extract.rtf_text(content)
        raw = contentmod.copyedit(date, c_text, ann_text, store=store, example=example)
        jobs.append(raw.pop("_job"))
        res = contentmod.postprocess(date, raw)
        week["subtitle_override"] = res["subtitle_override"]
        week["flowers"] = res["flowers"]
        week["announcements"] = res["announcements"]
        week["serve"] = {k: v for k, v in res["serve"].items() if v}
        week["calendar"] = res["calendar"]
        week["inserts"] = _inserts(res["announcements"], ann_text)
        week["layout"]["announcement_columns"] = None
        flags += res["flags"]
        changes += res["changes"]

    week["liturgical_day"] = dates.liturgical_day(date)
    week = weekmod.with_defaults(week)
    week["copyedit"] = {"changes": changes, "flags": flags}
    weekmod.save_week(week)
    store.set_week_status(date, "draft")
    cost = round(sum(j["cost"] for j in jobs), 4)
    store.log("intake", user, json.dumps({"date": date, "files": [str(p.name) for p in (liturgy, sermon, content, announcements) if p],
                                          "flags": len(flags), "changes": len(changes), "cost": cost}))
    return {"week": week, "flags": flags, "changes": changes, "jobs": jobs, "cost": cost}
