"""Model call 2: the office content form (+ pastor's announcements) → copyedited content.

Output: subtitle, flowers, announcements, serve, calendar, staff_changes,
plus the ``changes`` and ``flags`` lists the Proof page shows. The name
rules in ``names.py`` run again afterwards so a confirmed spelling always
wins even if the model missed it.
"""
from __future__ import annotations

import json
import re

from builder import config
from builder.pipeline import extract, model, names
from builder.pipeline.store import ROLE_LABELS, Store

_S = {"type": ["string", "null"]}
SERVE_KEYS = ["assisting_elder", "greeters", "ushers", "acolytes", "nursery", "circle_of_prayer", "childrens_church", "av_engineer"]
OUTPUT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["subtitle", "flowers", "announcements", "serve", "calendar", "staff_changes", "changes", "flags"],
    "properties": {
        "subtitle": _S,
        "flowers": {"type": ["object", "null"], "additionalProperties": False,
                    "required": ["given", "message", "quote", "reference", "closing", "signed"],
                    "properties": {"given": {"type": "string"}, "message": _S, "quote": _S, "reference": _S, "closing": _S, "signed": _S}},
        "announcements": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["title", "body", "print_suffix", "detail"],
            "properties": {"title": {"type": "string"}, "body": {"type": "string"}, "print_suffix": _S, "detail": _S}}},
        "serve": {"type": "object", "additionalProperties": False, "required": SERVE_KEYS,
                  "properties": {k: _S for k in SERVE_KEYS}},
        "calendar": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["date", "items"],
            "properties": {"date": {"type": "string"}, "items": {"type": "array", "items": {"type": "string"}}}}},
        "staff_changes": {"type": "array", "items": {"type": "string"}},
        "changes": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["where", "before", "after", "reason"],
            "properties": {k: {"type": "string"} for k in ("where", "before", "after", "reason")}}},
        "flags": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["where", "message"],
            "properties": {"where": {"type": "string"}, "message": {"type": "string"}}}},
    },
}

EXAMPLE_DATE = "2026-11-01"


def example_text(date: str = EXAMPLE_DATE) -> str | None:
    wdir = config.week_dir(date)
    week_path, content, ann = wdir / "week.json", wdir / f"bulletin-content-{date}.rtf", wdir / "Announcements.rtf"
    if not (week_path.exists() and content.exists()):
        return None
    week = json.loads(week_path.read_text())
    answer = {
        "subtitle": week.get("subtitle_override"),
        "flowers": week.get("flowers"),
        "announcements": [{"title": a["title"], "body": a["body"], "print_suffix": a.get("print_suffix"), "detail": a.get("detail")}
                          for a in week.get("announcements", [])],
        "serve": {k: week.get("serve", {}).get(k) for k in SERVE_KEYS},
        "calendar": [{"date": c["date"], "items": c["items"]} for c in week.get("calendar", [])],
        "staff_changes": [], "changes": [], "flags": [],
    }
    return ("## Worked example\n\nContent form:\n```\n" + extract.rtf_text(content).strip() + "\n```\n\n"
            + ("Pastor's announcements:\n```\n" + extract.rtf_text(ann).strip() + "\n```\n\n" if ann.exists() else "")
            + "Correct answer:\n```json\n" + json.dumps(answer, ensure_ascii=False, indent=1) + "\n```")


def system_prompt(store: Store, example: str | None = EXAMPLE_DATE) -> str:
    parts = [(config.PROMPTS_DIR / "content.md").read_text().strip()]
    spell = config.KNOWLEDGE_DIR / "name-spellings.md"
    if spell.exists():
        parts.append("## Confirmed name spellings\n\n" + spell.read_text().strip())
    staff = store.standing("staff") or []
    if staff:
        parts.append("## Standing staff list\n\n" + "\n".join(f"{s['name']} - {s['role']}" for s in staff))
    ex = example_text(example) if example else None
    if ex:
        parts.append(ex)
    return "\n\n".join(parts)


def copyedit(date: str, content_text: str, announcements_text: str | None, *, store: Store,
             job: model.Job | None = None, example: str | None = EXAMPLE_DATE) -> dict:
    job = job or model.Job("content", date, store)
    user = [f"Sunday: {date}.", "", "## Content form", "```", content_text.strip(), "```"]
    if announcements_text:
        user += ["", "## Pastor's announcements", "```", announcements_text.strip(), "```"]
    resp = model.complete(job, system=system_prompt(store, example), messages=[{"role": "user", "content": "\n".join(user)}],
                          schema=OUTPUT_SCHEMA, effort="medium", max_tokens=16000)
    out = model.json_of(resp)
    out["_job"] = {"id": job.id, "calls": job.calls, "cost": round(job.cost, 4), "log": job.log_lines}
    return out


_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def postprocess(date: str, parsed: dict) -> dict:
    """Shape the answer for week.json and re-run the name rules."""
    flags = list(parsed.get("flags", []))
    changes = list(parsed.get("changes", []))
    anns = []
    for a in parsed.get("announcements", []):
        clean = {"title": a["title"].strip(), "body": a["body"].strip()}
        if a.get("print_suffix"):
            clean["print_suffix"] = a["print_suffix"].strip()
        if a.get("detail"):
            clean["detail"] = a["detail"].strip()
        anns.append(clean)
    serve = {k: (v.strip() if isinstance(v, str) and v.strip() else None) for k, v in (parsed.get("serve") or {}).items() if k in SERVE_KEYS}
    for k, v in serve.items():
        if v is None and k not in ("childrens_church",):
            flags.append({"where": f"serve.{k}", "message": f"{ROLE_LABELS.get(k, k)} is blank on the content form."})
    calendar = []
    for c in parsed.get("calendar", []):
        if not _ISO.match(c.get("date", "")):
            flags.append({"where": "calendar", "message": f"Calendar line with unreadable date {c.get('date')!r}: {' · '.join(c.get('items', []))}"})
            continue
        if c["date"] < date:
            flags.append({"where": "calendar", "message": f"Calendar line dated {c['date']} is before the Sunday: {' · '.join(c['items'])}"})
        calendar.append({"date": c["date"], "items": [i.strip() for i in c.get("items", []) if i.strip()]})
    flowers = parsed.get("flowers")
    if flowers:
        flowers = {k: (v.strip() if isinstance(v, str) and v.strip() else None) for k, v in flowers.items()}
        if not flowers.get("given"):
            flowers = None
    out = {"subtitle_override": (parsed.get("subtitle") or "").strip() or None, "flowers": flowers, "announcements": anns,
           "serve": serve, "calendar": calendar, "staff_changes": parsed.get("staff_changes", []), "flags": flags, "changes": changes}
    for line in out["staff_changes"]:
        flags.append({"where": "staff", "message": f"Staff block differs from the standing list: {line}"})
    shell = {"serve": serve, "announcements": anns, "flowers": flowers, "calendar": calendar, "liturgy": {"items": []}, "sermon": {}}
    out["changes"] += names.fix_week(shell)
    return out
