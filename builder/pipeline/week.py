"""Load, validate and save ``bulletins/<date>/week.json``.

The week file is the single source of truth for a Sunday. Templates render
from it, the chat step edits it, and the archive keeps it. Every write goes
through :func:`save_week`, which validates against the JSON Schema first.
"""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import jsonschema

from builder import config
from builder.pipeline import dates

_SCHEMA: dict | None = None


def schema() -> dict:
    global _SCHEMA
    if _SCHEMA is None:
        _SCHEMA = json.loads(config.SCHEMA_PATH.read_text())
    return _SCHEMA


def validate(week: dict) -> None:
    """Raise jsonschema.ValidationError with a readable path on failure."""
    validator = jsonschema.Draft202012Validator(schema())
    errors = sorted(validator.iter_errors(week), key=lambda e: list(e.path))
    if errors:
        first = errors[0]
        where = "/".join(str(p) for p in first.path) or "<root>"
        raise ValueError(f"week.json invalid at {where}: {first.message}")
    ids = [it["id"] for it in week["liturgy"]["items"]]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise ValueError(f"week.json has duplicate liturgy item ids: {sorted(dupes)}")
    known = set(ids)
    for note in week["liturgy"].get("notes", []):
        if note["for"] not in known:
            raise ValueError(f"note for unknown item {note['for']!r}")
    pages = (week.get("layout") or {}).get("pages")
    if pages:
        placed = [i for p in ("p3", "p4", "p5", "p6") for i in pages.get(p, [])]
        missing = [i for i in ids if i not in placed]
        unknown = [i for i in placed if i not in known]
        if missing or unknown:
            raise ValueError(f"layout.pages mismatch; missing={missing} unknown={unknown}")
        if len(placed) != len(set(placed)):
            raise ValueError("layout.pages places an item twice")
        check_note_placements(week)


def check_note_placements(week: dict) -> None:
    """A spanned note cell must stay inside one page, must not cross a section
    heading (the grid would push the heading below the span) and must not
    cover an item whose own note is not in that cell (the note would vanish)."""
    items = items_by_id(week)
    note_ids = {n["for"] for n in week["liturgy"].get("notes", [])}
    placements = (week.get("layout") or {}).get("notes") or []
    in_cells = {nid for p in placements for nid in p["notes"]}
    for p in placements:
        if p["at"] not in items:
            raise ValueError(f"layout.notes: unknown item {p['at']!r}")
        for nid in p["notes"]:
            if nid not in note_ids:
                raise ValueError(f"layout.notes: no margin note for item {nid!r}")
    for page, page_ids in (week["layout"]["pages"] or {}).items():
        rows = [i for i in page_ids if items[i]["type"] != "section"]
        for p in placements:
            if p["at"] not in rows:
                continue
            start = rows.index(p["at"])
            covered = rows[start:start + p["span"]]
            if len(covered) < p["span"]:
                raise ValueError(f"layout.notes: span {p['span']} at {p['at']!r} runs past the end of {page}")
            first, last = page_ids.index(covered[0]), page_ids.index(covered[-1])
            crossed = [i for i in page_ids[first:last + 1] if items[i]["type"] == "section" and not items[i].get("scroll_only")]
            if crossed:
                raise ValueError(f"layout.notes: the span at {p['at']!r} crosses the {crossed[0]!r} heading; end it before the heading")
            for iid in covered:
                if iid in note_ids and iid not in p["notes"] and iid not in in_cells:
                    raise ValueError(f"layout.notes: the span at {p['at']!r} would hide the note for {iid!r}; include it in that cell or shorten the span")


def week_path(date: str) -> Path:
    return config.week_dir(date) / "week.json"


def load_week(date_or_path: str | Path) -> dict:
    path = Path(date_or_path)
    if not path.suffix:
        path = week_path(str(date_or_path))
    week = json.loads(path.read_text())
    week = with_defaults(week)
    validate(week)
    return week


def save_week(week: dict, path: Path | None = None) -> Path:
    validate(week)
    path = path or week_path(week["date"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(week, ensure_ascii=False, indent=2) + "\n")
    return path


def with_defaults(week: dict) -> dict:
    """Fill optional sections so templates never test for missing keys."""
    w = deepcopy(week)
    w.setdefault("liturgical_day", None)
    w.setdefault("subtitle_override", None)
    w.setdefault("special", {})
    w["special"].setdefault("communion", False)
    w["special"].setdefault("canvass", False)
    w.setdefault("pew_bible_page", None)
    w.setdefault("welcome_variant", "regular")
    w.setdefault("welcome_custom", None)
    w.setdefault("flowers", None)
    w.setdefault("announcements", [])
    w.setdefault("serve", {})
    w.setdefault("staff", None)
    w.setdefault("calendar", [])
    w["liturgy"].setdefault("intro", None)
    w["liturgy"].setdefault("notes", [])
    for it in w["liturgy"]["items"]:
        it.setdefault("star", False)
        it.setdefault("sub", False)
        it.setdefault("lines", [])
        it.setdefault("silent_confession", False)
    s = w["sermon"]
    s.setdefault("intro", [])
    s.setdefault("points", [])
    s.setdefault("notes_lines_per_point", 3)
    s.setdefault("blank_lines", 18)
    s.setdefault("missing", False)
    w.setdefault("inserts", [])
    layout = w.setdefault("layout", {})
    layout.setdefault("pages", None)
    layout.setdefault("page_style", {})
    layout.setdefault("announcement_columns", None)
    layout.setdefault("notes", [])
    layout.setdefault("overrides", [])
    if not layout["pages"]:
        layout["pages"] = default_pages(w["liturgy"]["items"])
    w.setdefault("copyedit", {"changes": [], "flags": []})
    return w


def subtitle(week: dict) -> str:
    """Cover subtitle: form override, then explicit liturgical_day, then computed."""
    return week.get("subtitle_override") or week.get("liturgical_day") or dates.liturgical_day(week["date"])


def default_pages(items: list[dict]) -> dict[str, list[str]]:
    """The usual shape: p3 through the first hymn, p4 the next two items,
    p5 through the item before the Scripture Reading, p6 the rest.
    The fit loop adjusts this when a page overflows."""
    ids = [it["id"] for it in items]
    types = [it["type"] for it in items]
    try:
        first_hymn = types.index("hymn")
    except ValueError:
        first_hymn = min(7, len(ids) - 1)
    p3_end = first_hymn + 1
    p4_end = min(p3_end + 2, len(ids))
    try:
        reading = types.index("reading", p4_end)
    except ValueError:
        reading = min(p4_end + 9, len(ids))
    return {
        "p3": ids[:p3_end],
        "p4": ids[p3_end:p4_end],
        "p5": ids[p4_end:reading],
        "p6": ids[reading:],
    }


def items_by_id(week: dict) -> dict[str, dict]:
    return {it["id"]: it for it in week["liturgy"]["items"]}


def notes_by_item(week: dict) -> dict[str, dict]:
    return {n["for"]: n for n in week["liturgy"].get("notes", [])}


def scaffold(date: str, *, serve: dict | None = None, calendar: list | None = None,
             preacher: str = "Rev. Waring Porter") -> dict:
    """A new week with the standing liturgy skeleton, ready for the form."""
    skeleton: list[dict[str, Any]] = [
        {"id": "gathering", "type": "section", "title": "Gathering"},
        {"id": "piano-prelude", "type": "plain", "title": "The Piano Prelude"},
        {"id": "greetings", "type": "plain", "title": "Greetings & Announcements"},
        {"id": "meditation", "type": "scripture", "title": "Call to Meditation", "ref": "", "text": ""},
        {"id": "prelude", "type": "plain", "title": "The Prelude"},
        {"id": "introit", "type": "music", "title": "Choral Introit", "piece": ""},
        {"id": "call-to-worship", "type": "responsive", "star": True, "title": "Call to Worship", "ref": "", "lines": []},
        {"id": "hymn1", "type": "hymn", "star": True, "title": "The Hymn", "number": None, "piece": ""},
        {"id": "invocation", "type": "corporate", "star": True, "title": "Prayer of Invocation & The Lord’s Prayer", "text": ""},
        {"id": "confession-call", "type": "responsive", "star": True, "title": "Scriptural Call to Confession", "ref": "", "lines": []},
        {"id": "confession", "type": "corporate", "star": True, "title": "Corporate Prayer of Confession", "silent_confession": True, "text": ""},
        {"id": "hymn2", "type": "hymn", "star": True, "title": "Hymn of Response", "number": None, "piece": ""},
        {"id": "assurance", "type": "scripture", "star": True, "title": "Assurance of Divine Pardon", "ref": "", "text": ""},
        {"id": "word", "type": "section", "title": "Word"},
        {"id": "tithes", "type": "plain", "title": "The Giving of Tithes and Offerings"},
        {"id": "anthem", "type": "music", "title": "Choral Anthem", "piece": ""},
        {"id": "doxology", "type": "corporate", "star": True, "title": "The Doxology and Prayer of Dedication", "text": ""},
        {"id": "children", "type": "plain", "star": True, "title": "Children Dismissed to Children’s Church"},
        {"id": "reading", "type": "reading", "title": "Scripture Reading", "ref": ""},
        {"id": "sermon", "type": "sermon", "title": "Sermon"},
        {"id": "commitment", "type": "plain", "title": "Prayer of Commitment"},
        {"id": "sending", "type": "section", "title": "Sending"},
        {"id": "hymn3", "type": "hymn", "star": True, "title": "Hymn", "number": None, "piece": ""},
        {"id": "benediction", "type": "plain", "star": True, "title": "Benediction"},
        {"id": "choral-benediction", "type": "music", "star": True, "title": "Choral Benediction", "piece": ""},
        {"id": "postlude", "type": "plain", "star": True, "title": "Postlude"},
    ]
    week = {
        "date": date,
        "liturgical_day": dates.liturgical_day(date),
        "subtitle_override": None,
        "special": {"communion": False, "canvass": False},
        "pew_bible_page": None,
        "welcome_variant": "regular",
        "flowers": None,
        "announcements": [],
        "serve": serve or {},
        "staff": None,
        "calendar": calendar or [],
        "liturgy": {"items": skeleton, "notes": []},
        "sermon": {"title": "", "scripture": "", "preacher": preacher, "intro": [], "points": [], "missing": True},
        "inserts": [],
        "layout": {"pages": None, "page_style": {}, "announcement_columns": None, "notes": [], "overrides": []},
        "copyedit": {"changes": [], "flags": []},
    }
    return with_defaults(week)
