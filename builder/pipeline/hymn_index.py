"""Hymn index: how often each hymn has been sung, when last, and where to read it.

A hymn counts once per service however many times it appears that day
(the congregation often sings verses 1-3, hears a reading, then sings 4-5).

The published scroll pages (``src/scroll/<date>.html``) are the record of
what was sung at each service, so the index is rebuilt from them (plus the
one older Markdown bulletin) rather than kept as a separate table. Every
publish writes ``src/_data/hymnIndex.json`` for the public ``/hymns/`` page;
the app's Hymns screen computes the same thing live.
"""
from __future__ import annotations

import html
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from builder import config
from builder.pipeline import dates

HYMNAL_ID = "HWC1986"
HYMNARY_URL = "https://hymnary.org/hymn/" + HYMNAL_ID + "/{number}"

_HEADING = re.compile(
    r'<p class="worship-heading">\*?\s*(?P<slot>[^<#]*?):?\s*(?:&nbsp;)?#(?P<number>\d+[a-i]?)\s*'
    r'<span class="worship-attr">&ldquo;(?P<title>.*?)&rdquo;(?P<rest>.*?)</span>', re.S)
_UNNUMBERED = re.compile(
    r'<p class="worship-heading">\*?\s*(?P<slot>[^<#]*Hymn[^<#]*?):\s*<span class="worship-attr">&ldquo;(?P<title>.*?)&rdquo;(?P<rest>.*?)</span>', re.S)
_VERSES = re.compile(r"\((v{1,2}\.[^)]*)\)")
_MD_HYMN = re.compile(r'- type: "Hymn"\s*\n\s*title: "(?P<title>[^"]*)"\s*\n\s*number: (?P<number>\d+)(?:\s*\n\s*verses: "(?P<verses>[^"]*)")?')


def _clean(s: str) -> str:
    return " ".join(html.unescape(s).split())


def _verses(rest: str) -> str | None:
    m = _VERSES.search(html.unescape(rest))
    return m.group(1).replace("–", "–") if m else None


def scan_scroll_page(text: str, date: str) -> tuple[list[dict], list[dict]]:
    """(numbered hymns, unnumbered hymns) sung on one page."""
    sung, unnumbered = [], []
    for m in _HEADING.finditer(text):
        slot = _clean(m.group("slot"))
        if "catechism" in slot.lower():
            continue
        sung.append({"date": date, "number": m.group("number"), "title": _clean(m.group("title")), "slot": slot, "verses": _verses(m.group("rest"))})
    for m in _UNNUMBERED.finditer(text):
        unnumbered.append({"date": date, "title": _clean(m.group("title")), "slot": _clean(m.group("slot")), "verses": _verses(m.group("rest"))})
    return sung, unnumbered


def scan_markdown_bulletin(text: str) -> list[dict]:
    m = re.search(r"^date: (\d{4}-\d{2}-\d{2})", text, re.M)
    if not m:
        return []
    date = m.group(1)
    return [{"date": date, "number": h.group("number"), "title": h.group("title"), "slot": "Hymn",
             "verses": ("vv. " + h.group("verses").replace(",", ", ")) if h.group("verses") else None} for h in _MD_HYMN.finditer(text)]


def scan(repo: Path | None = None) -> tuple[list[dict], list[dict]]:
    repo = repo or config.REPO
    sung, unnumbered = [], []
    for page in sorted((repo / "src/scroll").glob("????-??-??.html")):
        s, u = scan_scroll_page(page.read_text(), page.stem)
        sung += s
        unnumbered += u
    for md in sorted((repo / "src/bulletins").glob("????-??-??.md")):
        sung += scan_markdown_bulletin(md.read_text())
    sung.sort(key=lambda r: r["date"])
    unnumbered.sort(key=lambda r: r["date"])
    return sung, unnumbered


def _label(d: str) -> str:
    return f"{dates.month_day(d)}, {d[:4]}"


def _number_key(n: str) -> tuple[int, str]:
    m = re.match(r"(\d+)(.*)", n)
    return int(m.group(1)), m.group(2)


def build(repo: Path | None = None, hymns: dict | None = None) -> dict:
    """The index: one row per hymn number ever sung, sorted by number."""
    if hymns is None:
        path = config.STANDING_DIR / "hymns.json"
        hymns = json.loads(path.read_text()) if path.exists() else {}
    sung, unnumbered = scan(repo)
    by_number: dict[str, list[dict]] = {}
    for row in sung:
        uses = by_number.setdefault(row["number"], [])
        if uses and uses[-1]["date"] == row["date"]:
            # the same hymn split across a service (vv. 1-3, a reading, vv. 4-5) is one singing
            prev = uses[-1]
            prev["parts"] += 1
            if row["slot"] not in prev["slot"].split(" / "):
                prev["slot"] += " / " + row["slot"]
            if row["verses"]:
                more = re.sub(r"^v{1,2}\.\s*", "", row["verses"])
                prev["verses"] = f"{prev['verses']}, {more}" if prev["verses"] else row["verses"]
            continue
        uses.append({**row, "parts": 1})
    rows = []
    for number, uses in sorted(by_number.items(), key=lambda kv: _number_key(kv[0])):
        entry = hymns.get(number) or {}
        titles = []
        for u in uses:
            if u["title"] not in titles:
                titles.append(u["title"])
        rows.append({
            "number": number,
            "title": entry.get("title") or titles[0],
            "as_printed": [t for t in titles if t != entry.get("title")],
            "tune": entry.get("tune"),
            "in_hymnal": bool(entry),
            "times": len(uses),
            "first": uses[0]["date"],
            "last": uses[-1]["date"],
            "last_label": _label(uses[-1]["date"]),
            "dates": [{"date": u["date"], "label": _label(u["date"]), "slot": u["slot"], "verses": u["verses"], "parts": u["parts"]} for u in uses],
            "url": HYMNARY_URL.format(number=number),
        })
    services = sorted({r["date"] for r in sung} | {r["date"] for r in unnumbered})
    meta = hymns.get("_meta", {})
    return {
        "generated": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "hymnal": meta.get("hymnal", "The Hymnal for Worship & Celebration"),
        "hymnary_hymnal_url": "https://hymnary.org/hymnal/" + HYMNAL_ID,
        "services": len(services),
        "since": services[0] if services else None,
        "through": services[-1] if services else None,
        "since_label": _label(services[0]) if services else None,
        "through_label": _label(services[-1]) if services else None,
        "hymns": rows,
        "unnumbered": unnumbered,
    }


def write_site_data(repo: Path | None = None) -> Path:
    repo = repo or config.REPO
    path = repo / "src/_data/hymnIndex.json"
    path.write_text(json.dumps(build(repo), ensure_ascii=False, indent=2) + "\n")
    return path
