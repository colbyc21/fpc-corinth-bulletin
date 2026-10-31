"""The Friday Email: ``bulletins/<sunday>/friday.json`` → ``friday-<sunday>.html``.

The file is keyed by the Sunday it leads into so it sits beside that week's
``week.json`` and shares its calendar and roster. ``friday`` is the send
date (the Friday before). The HTML is a 600 px table layout with inline
styles, the shape Constant Contact's custom-code campaigns and most mail
clients want; it reproduces the hand-built emails of autumn 2026.
"""
from __future__ import annotations

import html
import json
import re
from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import jsonschema
from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup

from builder import config
from builder.pipeline import dates
from builder.pipeline import week as weekmod

SCHEMA_PATH = config.BUILDER_DIR / "pipeline/schema/friday.schema.json"
SECTION_ORDER = ["prepare", "give", "connect", "did_you_know", "announcements", "children", "youth", "women", "serve", "other"]
SECTION_DEFAULTS: dict[str, dict] = {
    "prepare": {"label": "Prepare"},
    "give": {"label": "Give"},
    "connect": {"label": "Connect"},
    "did_you_know": {"title": "Did You Know?"},
    "announcements": {"label": "Announcements and Updates to Mark Your Calendar", "large_headings": True, "split_items": True},
    "children": {"title": "Children’s Ministry", "footer_line": "Follow us on Instagram: @fpc_kidsquest"},
    "youth": {"title": "Youth Ministry", "footer_line": "Follow us on Instagram: @fpcyouthhousecorinth"},
    "women": {"title": "Women’s Ministry"},
    "serve": {"label": "Serve"},
    "other": {},
}
BIRTHDAYS_NOTE = ("If yours is not listed, or is incorrect, please go to the “Directory” app on your phone and "
                  "check/update your family’s profile! The app is how we access this information.")
STANDING = {
    "logo": "https://bulletin.fpccorinth.org/images/fpc-logo-2026.png",
    "bulletin_url": "https://bulletin.fpccorinth.org",
    "address": "919 East Shiloh Road · Corinth, MS 38834",
    "facebook": "https://www.facebook.com/fpccor/",
    "apple_podcasts": "https://podcasts.apple.com/us/podcast/fpc-corinth-sermons/id1894127322",
    "spotify": "https://open.spotify.com/show/5oUWooLtXqM8qIxoh2PHqf?si=PPhaVLyOTmiO5P7IRttDkA",
    "icon_facebook": "https://files.constantcontact.com/b85de079001/49166786-1b24-4c8d-aeae-a8e32270823e.png",
    "icon_apple": "https://files.constantcontact.com/b85de079001/f15cb7a2-56c9-4a54-8e46-d27a19a0bd8a.png",
    "icon_spotify": "https://files.constantcontact.com/b85de079001/e0ed6baf-78bd-4d09-ba1f-099973283719.png",
    "epc_logo": "https://files.constantcontact.com/b85de079001/a55a7144-4838-401d-8bac-17aec63ce5f0.jpg",
}

_SCHEMA: dict | None = None


def schema() -> dict:
    global _SCHEMA
    if _SCHEMA is None:
        _SCHEMA = json.loads(SCHEMA_PATH.read_text())
    return _SCHEMA


def validate(doc: dict) -> None:
    v = jsonschema.Draft202012Validator(schema())
    errors = sorted(v.iter_errors(doc), key=lambda e: list(e.path))
    if errors:
        first = errors[0]
        where = "/".join(str(p) for p in first.path) or "<root>"
        raise ValueError(f"friday.json invalid at {where}: {first.message}")
    keys = [s["key"] for s in doc["sections"]]
    if len(keys) != len(set(keys)):
        raise ValueError("friday.json has a repeated section key")


def path_for(sunday: str) -> Path:
    return config.week_dir(sunday) / "friday.json"


def html_path(sunday: str) -> Path:
    return config.week_dir(sunday) / f"friday-{sunday}.html"


def friday_of(sunday: str) -> str:
    return (date.fromisoformat(sunday) - timedelta(days=2)).isoformat()


def with_defaults(doc: dict) -> dict:
    d = deepcopy(doc)
    d.setdefault("friday", friday_of(d["date"]))
    d.setdefault("subject", "The Friday Email")
    d.setdefault("preheader", None)
    d.setdefault("scripture", None)
    d.setdefault("sections", [])
    d.setdefault("calendar", [])
    d.setdefault("birthdays", [])
    d.setdefault("anniversaries", [])
    d.setdefault("birthdays_note", BIRTHDAYS_NOTE)
    d.setdefault("serve", [])
    d.setdefault("closing_hymn", None)
    d.setdefault("copyedit", {"changes": [], "flags": []})
    d.setdefault("push", None)
    d.setdefault("media", [])
    for s in d["sections"]:
        base = SECTION_DEFAULTS.get(s["key"], {})
        for k in ("label", "title", "intro", "footer_line"):
            s.setdefault(k, base.get(k))
        s.setdefault("large_headings", base.get("large_headings", False))
        s.setdefault("split_items", base.get("split_items", False))
        s.setdefault("items", [])
        for it in s["items"]:
            for k in ("eyebrow", "title", "when", "note", "quote", "link", "image"):
                it.setdefault(k, None)
            it.setdefault("when_highlight", False)
            it.setdefault("body", [])
            it.setdefault("image_position", "after_body")
    return d


def load(sunday_or_path: str | Path) -> dict:
    p = Path(sunday_or_path)
    if not p.suffix:
        p = path_for(str(sunday_or_path))
    doc = with_defaults(json.loads(p.read_text()))
    validate(doc)
    return doc


def save(doc: dict, path: Path | None = None) -> Path:
    validate(doc)
    path = path or path_for(doc["date"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n")
    return path


def scaffold(sunday: str, week: dict | None = None, previous: dict | None = None) -> dict:
    """An empty email for the week, with the calendar and roster pulled from
    week.json when it exists and the standing sections in their usual order."""
    doc: dict[str, Any] = {"date": sunday, "friday": friday_of(sunday), "subject": "The Friday Email",
                           "sections": [{"key": k, "items": []} for k in SECTION_ORDER if k != "other"]}
    if week:
        doc["calendar"] = deepcopy(week.get("calendar") or [])
        doc["serve"] = serve_lines_from_week(week)
    if previous and previous.get("closing_hymn") is None:
        pass
    return with_defaults(doc)


def serve_lines_from_week(week: dict) -> list[dict]:
    from builder.pipeline.store import ROLE_LABELS

    order = ["assisting_elder", "greeters", "acolytes", "ushers", "nursery", "childrens_church", "circle_of_prayer", "av_engineer"]
    out = []
    for k in order:
        v = (week.get("serve") or {}).get(k)
        if v:
            out.append({"role": ROLE_LABELS.get(k, k.replace("_", " ").title()), "names": v})
    return out


# ── rendering ────────────────────────────────────────────────────────
_env: Environment | None = None


def env() -> Environment:
    global _env
    if _env is None:
        _env = Environment(loader=FileSystemLoader(str(config.TEMPLATES_DIR)), autoescape=select_autoescape(["html", "j2"]),
                           trim_blocks=True, lstrip_blocks=True)
        _env.filters["rich"] = rich
        _env.filters["month_day"] = dates.month_day
        _env.filters["long_date"] = dates.long_date
    return _env


_TEL = re.compile(r"\b(\d{3})-(\d{3})-(\d{4})\b")
_URL = re.compile(r"(?<![\"'>])\bhttps?://[^\s<)]+")
_HANDLE = re.compile(r"(?<!\w)(@[A-Za-z0-9_.]+)")


def rich(text: str) -> Markup:
    """Escape, then *italics*, phone numbers as tel: links, bare URLs as links, @handles in bold."""
    s = html.escape(text or "", quote=False)
    s = re.sub(r"\*([^*\n]+)\*", r"<em>\1</em>", s)
    s = _TEL.sub(lambda m: f'<a href="tel:{m.group(1)}{m.group(2)}{m.group(3)}" style="color: #56736a;">{m.group(0)}</a>', s)
    s = _URL.sub(lambda m: f'<a href="{m.group(0)}" target="_blank" style="color: #56736a;">{m.group(0)}</a>', s)
    s = _HANDLE.sub(r"<strong>\1</strong>", s)
    return Markup(s)


def render(doc: dict) -> str:
    doc = with_defaults(doc)
    sections = [s for s in doc["sections"] if s["items"]]
    ctx = {"e": doc, "sections": sections, "standing": STANDING,
           "friday_label": f"{dates.month_day(doc['friday'])}, {doc['friday'][:4]}",
           "band": lambda i: "#ffffff" if i % 2 == 0 else "#f1f4f2"}
    return env().get_template("email.html.j2").render(**ctx)


def build(sunday: str, doc: dict | None = None) -> Path:
    doc = doc or load(sunday)
    out = html_path(sunday)
    out.write_text(render(doc))
    return out


def text_of_html(markup: str) -> list[str]:
    """Visible text lines, for comparing a rendered email with a sent one."""
    body = re.sub(r"<(style|script)[^>]*>.*?</\1>", "", markup, flags=re.S)
    text = html.unescape(re.sub(r"<[^>]+>", "\n", body))
    return [" ".join(l.split()) for l in text.splitlines() if l.strip()]
