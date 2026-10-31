"""Model step for the Friday email: the office's notes → friday.json sections.

Mirrors ``content.py``: one strict-schema call, a deterministic merge with
the roster and calendar tables afterwards (they win; the notes add and
flag), then the name rules. ``tidy`` reruns the copyedit over text typed
into the web form.
"""
from __future__ import annotations

import json
import re
from copy import deepcopy
from datetime import date

from builder import config
from builder.pipeline import calendar_feed, dates, extract, friday, model, names
from builder.pipeline.store import ROLE_LABELS, Store

_S = {"type": "string"}  # "" means none: the API allows few nullable fields in a strict schema
_STRS = {"type": "array", "items": {"type": "string"}}
# Flat and null-free on purpose: nested or nullable fields made the strict-output grammar too large for the API.
_ITEM = {"type": "object", "additionalProperties": False,
         "required": ["eyebrow", "title", "when", "when_highlight", "body", "note", "quote_text", "quote_ref", "link_label", "link_url",
                      "image_src", "image_alt", "image_width"],
         "properties": {"eyebrow": _S, "title": _S, "when": _S, "when_highlight": {"type": "boolean"}, "body": _STRS, "note": _S,
                        "quote_text": _S, "quote_ref": _S, "link_label": _S, "link_url": _S, "image_src": _S, "image_alt": _S,
                        "image_width": {"type": "integer"}}}
_SECTION = {"type": "object", "additionalProperties": False, "required": ["key", "intro", "items"],
            "properties": {"key": {"type": "string", "enum": friday.SECTION_ORDER}, "intro": _S, "items": {"type": "array", "items": _ITEM}}}
_DATED = {"type": "object", "additionalProperties": False, "required": ["date", "names"],
          "properties": {"date": {"type": "string"}, "names": _STRS}}
OUTPUT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["scripture_text", "scripture_ref", "subject", "preheader", "sections", "birthdays", "anniversaries", "serve_lines",
                 "calendar_lines", "hymn_lines", "hymn_attribution", "carried_forward", "dropped", "changes", "flags"],
    "properties": {
        "scripture_text": _S, "scripture_ref": _S, "subject": _S, "preheader": _S,
        "sections": {"type": "array", "items": _SECTION},
        "birthdays": {"type": "array", "items": _DATED}, "anniversaries": {"type": "array", "items": _DATED},
        "serve_lines": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["role", "names"], "properties": {"role": _S, "names": {"type": "string"}}}},
        "calendar_lines": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["date", "items"], "properties": {"date": {"type": "string"}, "items": _STRS}}},
        "hymn_lines": _STRS, "hymn_attribution": _S,
        "carried_forward": _STRS, "dropped": _STRS,
        "changes": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["where", "before", "after", "reason"], "properties": {k: {"type": "string"} for k in ("where", "before", "after", "reason")}}},
        "flags": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["where", "message"], "properties": {"where": {"type": "string"}, "message": {"type": "string"}}}},
    },
}
EXAMPLE_DATE = "2026-11-01"
MODEL_KEYS = ("subject", "preheader", "birthdays", "anniversaries")


def _model_view(doc: dict) -> dict:
    """The parts of a friday.json the model produces, in its (flat) output shape."""
    out = {k: deepcopy(doc.get(k)) for k in MODEL_KEYS}
    sc = doc.get("scripture") or {}
    out["scripture_text"], out["scripture_ref"] = sc.get("text") or "", sc.get("ref") or ""
    out["subject"], out["preheader"] = doc.get("subject") or "", doc.get("preheader") or ""
    out["sections"] = []
    for s in doc.get("sections", []):
        items = []
        for it in s.get("items", []):
            q, l, im = it.get("quote") or {}, it.get("link") or {}, it.get("image") or {}
            items.append({"eyebrow": it.get("eyebrow") or "", "title": it.get("title") or "", "when": it.get("when") or "", "when_highlight": bool(it.get("when_highlight")),
                          "body": list(it.get("body", [])), "note": it.get("note") or "", "quote_text": q.get("text") or "", "quote_ref": q.get("ref") or "",
                          "link_label": l.get("label") or "", "link_url": l.get("url") or "", "image_src": im.get("src") or "", "image_alt": im.get("alt") or "",
                          "image_width": im.get("width") or 0})
        out["sections"].append({"key": s["key"], "intro": s.get("intro") or "", "items": items})
    hymn = doc.get("closing_hymn") or {}
    out["hymn_lines"], out["hymn_attribution"] = list(hymn.get("lines", [])), hymn.get("attribution") or ""
    out["serve_lines"] = [{"role": l.get("role") or "", "names": l.get("names") or ""} for l in doc.get("serve", [])]
    out["calendar_lines"] = deepcopy(doc.get("calendar", []))
    return out


def _nest(parsed: dict) -> dict:
    """The model's flat answer → the nested friday.json shapes merge() builds from."""
    out = dict(parsed)
    st = (parsed.get("scripture_text") or "").strip()
    out["scripture"] = {"text": st, "ref": (parsed.get("scripture_ref") or "").strip()} if st else None
    lines = [l for l in parsed.get("hymn_lines") or [] if l and l.strip()]
    out["closing_hymn"] = {"lines": lines, "attribution": parsed.get("hymn_attribution") or None} if lines else None
    out["sections"] = []
    for s in parsed.get("sections", []):
        items = []
        for it in s.get("items", []):
            qt, lu, src = (it.get("quote_text") or "").strip(), (it.get("link_url") or "").strip(), (it.get("image_src") or "").strip()
            qt = qt.strip("“”\"")  # the template sets the quotation marks
            img = None
            if src:
                img = {"src": src, "alt": (it.get("image_alt") or "Photo").strip()}
                if it.get("image_width"):
                    img["width"] = int(it["image_width"])
            items.append({"eyebrow": it.get("eyebrow") or None, "title": it.get("title") or None, "when": it.get("when") or None,
                          "when_highlight": bool(it.get("when_highlight")), "body": list(it.get("body") or []), "note": it.get("note") or None,
                          "quote": {"text": qt, "ref": (it.get("quote_ref") or "").strip()} if qt else None,
                          "link": {"label": (it.get("link_label") or "Learn more").strip(), "url": lu} if lu else None,
                          "image": img, "image_position": "after_body"})
        out["sections"].append({"key": s["key"], "label": None, "title": None, "intro": s.get("intro") or None, "footer_line": None, "items": items})
    out["serve_lines"] = [{"role": (l.get("role") or "").strip() or None, "names": l.get("names") or ""} for l in parsed.get("serve_lines", [])]
    out["subject"], out["preheader"] = parsed.get("subject") or None, parsed.get("preheader") or None
    return out


def example_text(sunday: str = EXAMPLE_DATE) -> str | None:
    wdir = config.week_dir(sunday)
    inp = wdir / f"friday-content-{friday.friday_of(sunday)}.txt"
    if not (inp.exists() and friday.path_for(sunday).exists()):
        return None
    doc = friday.load(sunday)
    answer = {**_model_view(doc), "carried_forward": [], "dropped": [], "changes": [], "flags": []}
    return ("## Worked example\n\nFor the email of " + friday.friday_of(sunday) + " (the Sunday is " + sunday + ") the office sent:\n\n```\n"
            + inp.read_text().strip() + "\n```\n\nand the correct answer, apart from `changes`, `flags`, `carried_forward` and `dropped`, was:\n\n```json\n"
            + json.dumps(answer, ensure_ascii=False, indent=1) + "\n```")


def system_prompt(store: Store, example: str | None = EXAMPLE_DATE) -> str:
    parts = [(config.PROMPTS_DIR / "friday.md").read_text().strip()]
    spell = config.KNOWLEDGE_DIR / "name-spellings.md"
    if spell.exists():
        parts.append("## Confirmed name spellings\n\n" + spell.read_text().strip())
    ex = example_text(example) if example else None
    if ex:
        parts.append(ex)
    return "\n\n".join(parts)


def _previous_summary(prev: dict | None) -> str | None:
    """Last week's items in the output shape (text included, so a carried-forward item keeps its wording)."""
    if not prev:
        return None
    view = _model_view(prev)
    sections = [s for s in view["sections"] if s["items"]]
    return json.dumps(sections, ensure_ascii=False, indent=1) if sections else None


def compose(sunday: str, office_text: str | None, *, store: Store, previous: dict | None = None, week: dict | None = None,
            structured: dict | None = None, job: model.Job | None = None, example: str | None = EXAMPLE_DATE) -> dict:
    """One model call. ``office_text`` is the notes; ``structured`` instead means
    copyedit this already-shaped content (the form path). Returns the raw answer plus ``_job``."""
    job = job or model.Job("content", f"friday:{sunday}", store)
    user = [f"Sunday: {sunday}. The email goes out Friday {friday.friday_of(sunday)}."]
    if structured is not None:
        user += ["", "The office typed the content straight into the web form, so it arrives already in the output shape. Copyedit it by the rules and "
                 "return it in the same shape: the same sections and items in the same order (never drop, merge or add one), dates unchanged. "
                 "`serve_lines` and `calendar_lines` are as typed; return them copyedited. `carried_forward` and `dropped` are empty.",
                 "", "## Content as typed", "```json", json.dumps(structured, ensure_ascii=False, indent=1), "```"]
    else:
        user += ["", "## The office's notes", "```", (office_text or "").strip(), "```"]
        prev = _previous_summary(previous)
        if prev:
            user += ["", f"## Last week's email ({previous['friday']}), item by item", prev]
        roster = store.serve_for(sunday) if store else (week or {}).get("serve") or {}
        serve = {ROLE_LABELS.get(k, k): v for k, v in roster.items() if v}
        cal = calendar_feed.week_calendar(store, sunday, horizon_days=14) if store else (week or {}).get("calendar") or []
        user += ["", "## The roster table for this Sunday", json.dumps(serve, ensure_ascii=False),
                 "", "## The calendar table's lines", json.dumps(cal, ensure_ascii=False)]
    resp = model.complete(job, system=system_prompt(store, example), messages=[{"role": "user", "content": "\n".join(user)}],
                          schema=OUTPUT_SCHEMA, effort="medium", max_tokens=24000)
    out = model.json_of(resp)
    out["_job"] = {"id": job.id, "calls": job.calls, "cost": round(job.cost, 4), "log": job.log_lines}
    return out


_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


_ROOM = re.compile(r"\broom\b|\binfants?\b|\bmovers\b|year olds|\b2\s*[-–]\s*3\b", re.I)


def _event_key(s: str) -> str:
    """What an event is, without its time: 'Messy Bible Study at 8 am' == 'Messy Bible Study, 8 a.m.'"""
    s = re.split(r",| at | @ |\s+\d", s, 1)[0]
    return re.sub(r"\b(the|a|an|for)\b", "", _norm(s)).replace("  ", " ").strip()


def merge(sunday: str, parsed: dict, *, store: Store, week: dict | None, base: dict | None = None) -> dict:
    """Shape the answer into friday.json: the model's sections, the roster and
    calendar tables for serve and calendar (the notes add and flag), name rules."""
    parsed = _nest(parsed)
    flags = list(parsed.get("flags", []))
    changes = list(parsed.get("changes", []))
    doc = deepcopy(base) if base else friday.scaffold(sunday, week)
    doc["scripture"] = parsed.get("scripture")
    doc["subject"] = (parsed.get("subject") or "").strip() or "The Friday Email"
    doc["preheader"] = (parsed.get("preheader") or "").strip() or None
    doc["closing_hymn"] = parsed.get("closing_hymn")
    sections = []
    for s in parsed.get("sections", []):
        items = []
        for it in s.get("items", []):
            clean = {k: v for k, v in it.items()}
            clean["body"] = [p.strip() for p in it.get("body", []) if p and p.strip()]
            if clean.get("image") and clean["image"].get("width") is None:
                clean["image"] = {"src": clean["image"]["src"], "alt": clean["image"]["alt"]}
            if not (clean["body"] or clean.get("title") or clean.get("quote") or clean.get("image") or clean.get("link")):
                continue
            items.append(clean)
        if items:
            sections.append({"key": s["key"], "label": s.get("label"), "title": s.get("title"), "intro": s.get("intro"),
                             "footer_line": s.get("footer_line"), "items": items})
    sections.sort(key=lambda s: friday.SECTION_ORDER.index(s["key"]))
    doc["sections"] = sections

    def dated(rows, where):
        out = []
        for r in rows:
            if not _ISO.match(r.get("date", "")):
                flags.append({"where": where, "message": f"Unreadable date {r.get('date')!r} for {', '.join(r.get('names', []))}"})
                continue
            out.append({"date": r["date"], "names": [n.strip() for n in r.get("names", []) if n.strip()]})
        return sorted(out, key=lambda r: r["date"])
    doc["birthdays"] = dated(parsed.get("birthdays", []), "birthdays")
    doc["anniversaries"] = dated(parsed.get("anniversaries", []), "anniversaries")

    # calendar: the table's lines for two weeks (plus featured events further out), then what the notes add
    table = calendar_feed.week_calendar(store, sunday, horizon_days=14) if store else (week or {}).get("calendar") or []
    cal = [{"date": c["date"], "items": list(c["items"])} for c in table]
    known = {(c["date"], _event_key(i)) for c in cal for i in c["items"]}
    for line in parsed.get("calendar_lines", []):
        if not _ISO.match(line.get("date", "")):
            flags.append({"where": "calendar", "message": f"Calendar line with unreadable date {line.get('date')!r}: {' · '.join(line.get('items', []))}"})
            continue
        for item in line.get("items", []):
            item = item.strip()
            if not item or (line["date"], _event_key(item)) in known:
                continue
            row = next((c for c in cal if c["date"] == line["date"]), None)
            if row is None:
                row = {"date": line["date"], "items": []}
                cal.append(row)
            row["items"].append(item)
            known.add((line["date"], _event_key(item)))
            flags.append({"where": "calendar", "message": f"{dates.month_day(line['date'])}: “{item}” is in the office's notes but not in the calendar table; add it there so the bulletin and the feed carry it."})
    cal.sort(key=lambda c: c["date"])
    doc["calendar"] = [c for c in cal if c["items"]]

    # serve: the roster row, with the notes' extras (nursery rooms, deliverers) and disagreements flagged
    roster = store.serve_for(sunday) if store else (week or {}).get("serve") or {}
    lines = friday.serve_lines_from_week({"serve": roster})
    by_role = {l["role"].lower(): l for l in lines}
    extras: list[dict] = []
    nursery_rooms: list[dict] = []
    last_role = None
    for ln in parsed.get("serve_lines", []):
        role, nm = (ln.get("role") or None), (ln.get("names") or "").strip()
        if role is None:
            (nursery_rooms if last_role == "nursery" or _ROOM.search(nm) else extras).append({"role": None, "names": nm})
            continue
        last_role = role.lower()
        if last_role == "nursery" and _ROOM.search(nm):  # "Nursery — Infant (Room 4) — …" came through as one line
            nursery_rooms.append({"role": None, "names": nm})
            continue
        if last_role in by_role:
            table_names = by_role[last_role]["names"]
            if nm and _norm(nm) != _norm(table_names):
                flags.append({"where": f"serve.{last_role}", "message": f"{role}: the notes say “{nm}” but the roster table says “{table_names}”; the table was used."})
        elif last_role in {v.lower() for v in ROLE_LABELS.values()}:
            if nm:
                lines.append({"role": role, "names": nm})
                by_role[last_role] = lines[-1]
                flags.append({"where": f"serve.{last_role}", "message": f"{role} is blank in the roster table; used the notes' “{nm}”. Fill the roster so the bulletin matches."})
        else:
            extras.append({"role": role, "names": nm})
    if nursery_rooms:
        merged = []
        for l in lines:
            merged.append({"role": "Nursery", "names": ""} if l["role"] == "Nursery" else l)
            if l["role"] == "Nursery":
                merged.extend(nursery_rooms)
        if not any(l["role"] == "Nursery" for l in lines):
            merged.append({"role": "Nursery", "names": ""})
            merged.extend(nursery_rooms)
        lines = merged
    doc["serve"] = lines + extras

    # name rules over the free text
    for si, s in enumerate(doc["sections"]):
        for ii, it in enumerate(s["items"]):
            for fld in ("title", "eyebrow", "note"):
                it[fld], ch = names.fix_text(it.get(fld), f"sections[{si}].items[{ii}].{fld}")
                changes += [c.as_dict() for c in ch]
            for pi, p in enumerate(it["body"]):
                it["body"][pi], ch = names.fix_text(p, f"sections[{si}].items[{ii}].body[{pi}]")
                changes += [c.as_dict() for c in ch]
    for li, l in enumerate(doc["serve"]):
        l["names"], ch = names.fix_text(l["names"], f"serve[{li}]")
        l["names"] = l["names"] or ""
        changes += [c.as_dict() for c in ch]
    for what in ("carried_forward", "dropped"):
        for t in parsed.get(what, []):
            flags.append({"where": what, "message": ("Carried forward from last week: " if what == "carried_forward" else "Dropped as past: ") + t})
    doc["copyedit"] = {"changes": changes, "flags": flags}
    doc = friday.with_defaults(doc)
    friday.validate(doc)
    return doc


def intake(sunday: str, office_path, *, store: Store, user: str | None = None, example: str | None = EXAMPLE_DATE) -> dict:
    """Read the office's notes file into friday.json for the week."""
    from pathlib import Path

    p = Path(office_path)
    text = extract.rtf_text(p) if p.suffix.lower() == ".rtf" else p.read_text(errors="replace")
    week = None
    try:
        from builder.pipeline import week as weekmod
        week = weekmod.load_week(sunday)
    except Exception:
        pass
    previous = _previous(sunday)
    raw = compose(sunday, text, store=store, previous=previous, week=week, example=example)
    jobinfo = raw.pop("_job")
    base = friday.load(sunday) if friday.path_for(sunday).exists() else None
    doc = merge(sunday, raw, store=store, week=week, base=base)
    if base:
        doc["media"] = base.get("media", [])
        doc["push"] = base.get("push")
    friday.save(doc)
    store.log("friday.intake", user, json.dumps({"date": sunday, "file": p.name, "cost": jobinfo["cost"], "flags": len(doc["copyedit"]["flags"])}))
    return {"doc": doc, "cost": jobinfo["cost"], "job": jobinfo}


def _previous(sunday: str) -> dict | None:
    from datetime import timedelta

    d = date.fromisoformat(sunday)
    for back in (7, 14):
        p = friday.path_for((d - timedelta(days=back)).isoformat())
        if p.exists():
            try:
                return friday.load(p)
            except Exception:
                return None
    return None


def tidy(sunday: str, doc: dict, *, store: Store, example: str | None = EXAMPLE_DATE) -> dict:
    """Copyedit the typed content (form path). Returns the merged doc plus ``_job``."""
    raw = compose(sunday, None, store=store, structured=_model_view(doc), example=example)
    jobinfo = raw.pop("_job")
    week = None
    try:
        from builder.pipeline import week as weekmod
        week = weekmod.load_week(sunday)
    except Exception:
        pass
    out = merge(sunday, raw, store=store, week=week, base=doc)
    # the form path keeps the typed serve/calendar verbatim if the model changed their shape
    if len(raw.get("serve_lines", [])) == len(doc.get("serve", [])):
        out["serve"] = [{"role": a.get("role"), "names": (b.get("names") or a["names"])} for a, b in zip(doc["serve"], raw["serve_lines"])]
    if [c["date"] for c in raw.get("calendar_lines", [])] == [c["date"] for c in doc.get("calendar", [])]:
        out["calendar"] = [{"date": c["date"], "items": [i for i in c["items"] if i.strip()]} for c in raw["calendar_lines"]]
    out["media"] = doc.get("media", [])
    out["push"] = doc.get("push")
    friday.validate(out)
    out["_job"] = jobinfo
    return out
