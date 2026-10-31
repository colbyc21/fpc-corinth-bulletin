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

_S = {"type": ["string", "null"]}
_IMAGE = {"type": ["object", "null"], "additionalProperties": False, "required": ["src", "alt", "width"],
          "properties": {"src": {"type": "string"}, "alt": {"type": "string"}, "width": {"type": ["integer", "null"]}}}
_ITEM = {"type": "object", "additionalProperties": False,
         "required": ["eyebrow", "title", "when", "when_highlight", "body", "note", "quote", "link", "image", "image_position"],
         "properties": {"eyebrow": _S, "title": _S, "when": _S, "when_highlight": {"type": "boolean"},
                        "body": {"type": "array", "items": {"type": "string"}}, "note": _S,
                        "quote": {"type": ["object", "null"], "additionalProperties": False, "required": ["text", "ref"], "properties": {"text": {"type": "string"}, "ref": {"type": "string"}}},
                        "link": {"type": ["object", "null"], "additionalProperties": False, "required": ["label", "url"], "properties": {"label": {"type": "string"}, "url": {"type": "string"}}},
                        "image": _IMAGE, "image_position": {"type": "string", "enum": ["after_body", "before_body", "after_note"]}}}
_SECTION = {"type": "object", "additionalProperties": False, "required": ["key", "label", "title", "intro", "footer_line", "items"],
            "properties": {"key": {"type": "string", "enum": friday.SECTION_ORDER}, "label": _S, "title": _S, "intro": _S, "footer_line": _S,
                           "items": {"type": "array", "items": _ITEM}}}
_DATED = {"type": "object", "additionalProperties": False, "required": ["date", "names"],
          "properties": {"date": {"type": "string"}, "names": {"type": "array", "items": {"type": "string"}}}}
OUTPUT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["scripture", "subject", "preheader", "sections", "birthdays", "anniversaries", "serve_lines", "calendar_lines",
                 "closing_hymn", "carried_forward", "dropped", "changes", "flags"],
    "properties": {
        "scripture": {"type": ["object", "null"], "additionalProperties": False, "required": ["text", "ref"], "properties": {"text": {"type": "string"}, "ref": {"type": "string"}}},
        "subject": _S, "preheader": _S,
        "sections": {"type": "array", "items": _SECTION},
        "birthdays": {"type": "array", "items": _DATED}, "anniversaries": {"type": "array", "items": _DATED},
        "serve_lines": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["role", "names"], "properties": {"role": _S, "names": {"type": "string"}}}},
        "calendar_lines": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["date", "items"], "properties": {"date": {"type": "string"}, "items": {"type": "array", "items": {"type": "string"}}}}},
        "closing_hymn": {"type": ["object", "null"], "additionalProperties": False, "required": ["lines", "attribution"], "properties": {"lines": {"type": "array", "items": {"type": "string"}}, "attribution": _S}},
        "carried_forward": {"type": "array", "items": {"type": "string"}}, "dropped": {"type": "array", "items": {"type": "string"}},
        "changes": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["where", "before", "after", "reason"], "properties": {k: {"type": "string"} for k in ("where", "before", "after", "reason")}}},
        "flags": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["where", "message"], "properties": {"where": {"type": "string"}, "message": {"type": "string"}}}},
    },
}
EXAMPLE_DATE = "2026-11-01"
MODEL_KEYS = ("scripture", "subject", "preheader", "sections", "birthdays", "anniversaries", "closing_hymn")


def _model_view(doc: dict) -> dict:
    """The parts of a friday.json the model produces, in its output shape."""
    out = {k: deepcopy(doc.get(k)) for k in MODEL_KEYS}
    for s in out["sections"]:
        for k in ("label", "title", "intro", "footer_line"):
            s.setdefault(k, None)
        s.pop("large_headings", None)
        s.pop("split_items", None)
        for it in s["items"]:
            for k in ("eyebrow", "title", "when", "note", "quote", "link", "image"):
                it.setdefault(k, None)
            it.setdefault("when_highlight", False)
            it.setdefault("image_position", "after_body")
            if it["image"] is not None:
                it["image"].setdefault("width", None)
    out["serve_lines"] = deepcopy(doc.get("serve", []))
    out["calendar_lines"] = deepcopy(doc.get("calendar", []))
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
    if not prev:
        return None
    lines = []
    for s in prev.get("sections", []):
        for it in s.get("items", []):
            head = it.get("title") or it.get("eyebrow") or (it.get("body") or [""])[0][:60]
            lines.append(f"- [{s['key']}] {head}" + (f" ({it['when']})" if it.get("when") else ""))
    return "\n".join(lines) or None


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
        if week:
            serve = {ROLE_LABELS.get(k, k): v for k, v in (week.get("serve") or {}).items() if v}
            user += ["", "## The roster table for this Sunday", json.dumps(serve, ensure_ascii=False),
                     "", "## The calendar table's lines", json.dumps(week.get("calendar") or [], ensure_ascii=False)]
    resp = model.complete(job, system=system_prompt(store, example), messages=[{"role": "user", "content": "\n".join(user)}],
                          schema=OUTPUT_SCHEMA, effort="medium", max_tokens=24000)
    out = model.json_of(resp)
    out["_job"] = {"id": job.id, "calls": job.calls, "cost": round(job.cost, 4), "log": job.log_lines}
    return out


_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def _event_key(s: str) -> str:
    """What an event is, without its time: 'Messy Bible Study at 8 am' == 'Messy Bible Study, 8 a.m.'"""
    s = re.split(r",| at | @ |\s+\d", s, 1)[0]
    return _norm(s)


def merge(sunday: str, parsed: dict, *, store: Store, week: dict | None, base: dict | None = None) -> dict:
    """Shape the answer into friday.json: the model's sections, the roster and
    calendar tables for serve and calendar (the notes add and flag), name rules."""
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
            (nursery_rooms if last_role == "nursery" else extras).append({"role": None, "names": nm})
            continue
        last_role = role.lower()
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
