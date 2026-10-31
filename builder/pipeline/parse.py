"""Model call 1: Liturgy docx (+ Sermon Notes docx) → liturgy, sermon, special.

The model gets the prompt in ``builder/prompts/parse.md``, the knowledge
notes, an example week (the 2026-11-01 documents and their week.json) and the
output schema; the answer is guaranteed to match the schema by the API.
``postprocess`` then does the deterministic parts: the standing Greetings
item, the hymnal check, the Pew Bible page, house punctuation, name fixes.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from builder import config
from builder.pipeline import extract, model, names
from builder.pipeline.store import Store

ITEM_TYPES = ["section", "plain", "scripture", "responsive", "corporate", "hymn", "music", "speaker", "reading", "sermon"]

_S = {"type": ["string", "null"]}
ITEM_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["id", "type", "title", "star", "sub", "ref", "text", "lines", "silent_confession", "creed_note",
                 "number", "piece", "credit", "name", "role", "scroll_only"],
    "properties": {
        "id": {"type": "string"},
        "type": {"type": "string", "enum": ITEM_TYPES},
        "title": {"type": "string"},
        "star": {"type": "boolean"},
        "sub": {"type": "boolean"},
        "ref": _S, "text": _S,
        "lines": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["who", "text"],
                                             "properties": {"who": {"type": "string"}, "text": {"type": "string"}}}},
        "silent_confession": {"type": "boolean"},
        "creed_note": _S,
        "number": {"type": ["integer", "null"]},
        "piece": _S, "credit": _S, "name": _S, "role": _S,
        "scroll_only": {"type": "boolean"},
    },
}
FLAG_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["where", "message"],
               "properties": {"where": {"type": "string"}, "message": {"type": "string"}}}
OUTPUT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["liturgy", "sermon", "special", "flags"],
    "properties": {
        "liturgy": {"type": "object", "additionalProperties": False, "required": ["items", "notes"],
                    "properties": {"items": {"type": "array", "items": ITEM_SCHEMA},
                                   "notes": {"type": "array", "items": {
                                       "type": "object", "additionalProperties": False, "required": ["for", "label", "text"],
                                       "properties": {"for": {"type": "string"}, "label": {"type": "string"}, "text": {"type": "string"}}}}}},
        "sermon": {"type": "object", "additionalProperties": False,
                   "required": ["title", "scripture", "preacher", "intro", "points", "missing"],
                   "properties": {"title": {"type": "string"}, "scripture": {"type": "string"}, "preacher": {"type": "string"},
                                  "intro": {"type": "array", "items": {"type": "string"}},
                                  "points": {"type": "array", "items": {
                                      "type": "object", "additionalProperties": False, "required": ["heading", "ref", "quote", "sub"],
                                      "properties": {"heading": {"type": "string"}, "ref": _S, "quote": _S,
                                                     "sub": {"type": "array", "items": {"type": "string"}}}}},
                                  "missing": {"type": "boolean"}}},
        "special": {"type": "object", "additionalProperties": False, "required": ["communion", "canvass"],
                    "properties": {"communion": {"type": "boolean"}, "canvass": {"type": "boolean"}}},
        "flags": {"type": "array", "items": FLAG_SCHEMA},
    },
}

EXAMPLE_DATE = "2026-11-01"


def knowledge_text() -> str:
    parts = []
    for name in ("weekly-bulletin-workflow.md", "name-spellings.md"):
        p = config.KNOWLEDGE_DIR / name
        if p.exists():
            parts.append(f"### {name}\n{p.read_text().strip()}")
    return "\n\n".join(parts)


def example_text(date: str = EXAMPLE_DATE) -> str | None:
    """The example week's documents and the matching parts of its week.json."""
    wdir = config.week_dir(date)
    week_path = wdir / "week.json"
    lit = wdir / f"{date} Liturgy.docx"
    if not (week_path.exists() and lit.exists()):
        return None
    week = json.loads(week_path.read_text())
    sermon = wdir / f"{date} Sermon Notes.docx"
    answer = {"liturgy": week["liturgy"], "sermon": {k: v for k, v in week["sermon"].items() if k in ("title", "scripture", "preacher", "intro", "points", "missing")},
              "special": week.get("special", {}), "flags": []}
    answer["sermon"].setdefault("missing", False)
    return ("## Worked example\n\nLiturgy document:\n```\n" + extract.docx_text(lit).strip() + "\n```\n\n"
            + ("Sermon Notes document:\n```\n" + extract.docx_text(sermon).strip() + "\n```\n\n" if sermon.exists() else "")
            + "Correct answer:\n```json\n" + json.dumps(answer, ensure_ascii=False, indent=1) + "\n```")


def system_prompt(example: str | None = EXAMPLE_DATE) -> str:
    parts = [(config.PROMPTS_DIR / "parse.md").read_text().strip(),
             "## Knowledge notes (how the bulletin is produced; apply what is relevant)\n\n" + knowledge_text()]
    ex = example_text(example) if example else None
    if ex:
        parts.append(ex)
    return "\n\n".join(parts)


def parse_documents(date: str, liturgy_text: str, sermon_text: str | None, *, store: Store,
                    job: model.Job | None = None, example: str | None = EXAMPLE_DATE) -> dict:
    """Run the model on the extracted text. Returns the raw structured answer."""
    job = job or model.Job("parse", date, store)
    user = [f"Sunday: {date}.", "", "## Liturgy document", "```", liturgy_text.strip(), "```"]
    if sermon_text:
        user += ["", "## Sermon Notes document", "```", sermon_text.strip(), "```"]
    else:
        user += ["", "There is no Sermon Notes document this week."]
    resp = model.complete(job, system=system_prompt(example), messages=[{"role": "user", "content": "\n".join(user)}],
                          schema=OUTPUT_SCHEMA, effort="medium", max_tokens=24000)
    out = model.json_of(resp)
    out["_job"] = {"id": job.id, "calls": job.calls, "cost": round(job.cost, 4), "log": job.log_lines}
    return out


# ── deterministic post-processing ────────────────────────────────────

def _norm(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def hymnal_check(items: list[dict], hymns: dict | None) -> list[dict]:
    """Flag a hymn whose number and title disagree with the hymnal table."""
    flags = []
    if not hymns:
        return flags
    by_title: dict[str, str] = {}
    for num, h in hymns.items():
        if num.startswith("_"):
            continue
        for t in [h.get("title")] + list(h.get("variants", [])) + [h.get("first_line")]:
            if t:
                by_title.setdefault(_norm(t), num)
    for it in items:
        if it.get("type") != "hymn" or not it.get("number"):
            continue
        h = hymns.get(str(it["number"]))
        piece = _norm(it.get("piece"))
        if not h:
            flags.append({"where": f"liturgy.{it['id']}", "message": f"#{it['number']} is not in the hymnal table."})
            continue
        known = {_norm(h.get("title"))} | {_norm(v) for v in h.get("variants", [])}
        first = _norm(h.get("first_line"))
        if piece and piece not in known and not (first and (first.startswith(piece) or piece.startswith(first[:12]))):
            other = by_title.get(piece)
            msg = f"#{it['number']} is “{h.get('title')}” in the hymnal; the liturgy says “{it.get('piece')}”"
            msg += f", which is #{other}." if other else "."
            flags.append({"where": f"liturgy.{it['id']}", "message": msg})
    return flags


def pew_bible_page(scripture: str, table: dict | None) -> tuple[int | None, str | None]:
    """Exact reference first, then the same book and chapter; else None and why."""
    if not scripture or not table:
        return None, f"No Pew Bible page table entry for “{scripture}”; page omitted." if scripture else None
    want = _norm(scripture)
    for ref, entry in table.items():
        if _norm(ref) == want:
            return int(entry["page"]), None
    m = re.match(r"\s*([1-3]?\s*[A-Za-z]+)\s+(\d+)", scripture)
    if m:
        book, chap = _norm(m.group(1)), m.group(2)
        for ref, entry in table.items():
            m2 = re.match(r"\s*([1-3]?\s*[A-Za-z]+)\s+(\d+)", ref)
            if m2 and _norm(m2.group(1)) == book and m2.group(2) == chap:
                return int(entry["page"]), None
    return None, f"No Pew Bible page known for {scripture}; page omitted."


_DASH_RANGE = re.compile(r"(\d)-(\d)")


def house_punctuation(s: str | None) -> str | None:
    if not s:
        return s
    s = s.replace(" ", " ")
    s = re.sub(r"[ \t]{2,}", " ", s)
    s = s.replace("'", "’")
    s = re.sub(r'"(.*?)"', lambda m: "“" + m.group(1) + "”", s, flags=re.S)
    return s.strip()


def postprocess(date: str, parsed: dict, store: Store) -> dict:
    """Clean the model's answer into week.json shape; returns {liturgy, sermon, special, pew_bible_page, flags, changes}."""
    flags = list(parsed.get("flags", []))
    items = []
    for it in parsed["liturgy"]["items"]:
        clean = {k: v for k, v in it.items() if v not in (None, False, [], "")}
        clean.setdefault("star", False)
        for k in ("ref", "text", "piece", "credit", "name", "role", "creed_note"):
            if k in clean:
                clean[k] = house_punctuation(clean[k])
                if k == "ref":
                    clean[k] = _DASH_RANGE.sub(r"\1–\2", clean[k])
        for ln in clean.get("lines", []):
            ln["text"] = house_punctuation(ln["text"])
        if clean["type"] == "hymn" and clean.get("ref") and re.match(r"^\(?(vv?\.|verses?)\s*[\d–,\s-]+\)?$", clean["ref"].strip(), re.I):
            clean["piece"] = f"{clean.get('piece') or ''} ({clean.pop('ref').strip('() ')})".strip()
        items.append(clean)
    ids = [it["id"] for it in items]
    if "greetings" not in ids:
        at = ids.index("piano-prelude") + 1 if "piano-prelude" in ids else 1
        items.insert(at, {"id": "greetings", "type": "plain", "title": "Greetings & Announcements"})
    seen: set[str] = set()
    for it in items:
        base = it["id"]
        n = 2
        while it["id"] in seen:
            it["id"] = f"{base}-{n}"
            n += 1
        seen.add(it["id"])
    notes = []
    for n in parsed["liturgy"].get("notes", []):
        if n["for"] not in seen:
            flags.append({"where": f"liturgy.notes[{n['label']}]", "message": f"Note “{n['label']}” refers to unknown item {n['for']!r}; dropped."})
            continue
        notes.append({"for": n["for"], "label": house_punctuation(n["label"]).rstrip(":"), "text": house_punctuation(n["text"])})
    sermon = dict(parsed["sermon"])
    sermon["title"] = house_punctuation(sermon["title"])
    sermon["scripture"] = _DASH_RANGE.sub(r"\1–\2", house_punctuation(sermon["scripture"]))
    sermon["intro"] = [house_punctuation(p) for p in sermon.get("intro", []) if p and p.strip()]
    pts = []
    for p in sermon.get("points", []):
        q = {k: v for k, v in p.items() if v not in (None, [], "")}
        for k in ("heading", "ref", "quote"):
            if k in q:
                q[k] = _DASH_RANGE.sub(r"\1–\2", house_punctuation(q[k]))
        pts.append(q)
    sermon["points"] = pts
    sermon["missing"] = bool(sermon.get("missing")) or not (sermon["intro"] or sermon["points"])
    if sermon["missing"]:
        flags.append({"where": "sermon", "message": "No sermon notes this week; the notes page is a fill-in page."})
    flags += hymnal_check(items, store.standing("hymns"))
    page, why = pew_bible_page(sermon["scripture"], store.standing("pew-bible-pages"))
    if why:
        flags.append({"where": "pew_bible_page", "message": why})
    out = {"liturgy": {"items": items, "notes": notes}, "sermon": sermon,
           "special": {"communion": bool(parsed["special"].get("communion")), "canvass": bool(parsed["special"].get("canvass"))},
           "pew_bible_page": page, "flags": flags, "changes": []}
    shell = {"liturgy": out["liturgy"], "sermon": sermon, "serve": {}, "announcements": [], "calendar": []}
    out["changes"] = names.fix_week(shell)
    return out
