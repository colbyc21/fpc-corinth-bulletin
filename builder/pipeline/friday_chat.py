"""Chat corrections for the Friday email (same shape as ``chat.py``)."""
from __future__ import annotations

import json

from builder import config
from builder.pipeline import chat as weekchat, friday, model, names
from builder.pipeline.store import Store

MAX_MESSAGES = 30
HISTORY_TURNS = 6
TOOLS = [
    {"name": "read_email", "description": "The whole friday.json for this week.",
     "strict": True, "input_schema": {"type": "object", "additionalProperties": False, "properties": {}, "required": []}},
    {"name": "edit_email", "description": "Set one value in friday.json by JSON pointer path. value is JSON text; null clears; \"__delete__\" removes an array element or key; a path ending in /- appends to an array.",
     "strict": True, "input_schema": {"type": "object", "additionalProperties": False, "required": ["path", "value"],
                                      "properties": {"path": {"type": "string"}, "value": {"type": "string"}}}},
    {"name": "render_email", "description": "Re-render the email HTML from friday.json and return a text outline of it.",
     "strict": True, "input_schema": {"type": "object", "additionalProperties": False, "properties": {}, "required": []}},
]
PROTECTED = ("/date", "/friday", "/copyedit", "/push", "/media")


def chat_key(sunday: str) -> str:
    return f"{sunday}#email"


def apply_edit(doc: dict, path: str, value_text: str) -> tuple[dict, str]:
    for p in PROTECTED:
        if path == p or path.startswith(p + "/"):
            raise ValueError(f"{p} is not editable here")
    try:
        value = json.loads(value_text)
    except json.JSONDecodeError:
        value = value_text
    from copy import deepcopy
    new = deepcopy(doc)
    delete = value == "__delete__"
    weekchat.pointer_set(new, path, None if delete else value, delete=delete)
    new = friday.with_defaults(new)
    changes = []
    for si, s in enumerate(new["sections"]):
        for ii, it in enumerate(s["items"]):
            for pi, para in enumerate(it["body"]):
                it["body"][pi], ch = names.fix_text(para, f"sections[{si}].items[{ii}].body[{pi}]")
                changes += ch
    for l in new["serve"]:
        l["names"], ch = names.fix_text(l["names"], "serve")
        l["names"] = l["names"] or ""
        changes += ch
    friday.validate(new)
    note = f"set {path}" + (f"; name rule applied: {changes[0].before} → {changes[0].after}" if changes else "")
    return new, note


def _system() -> str:
    parts = [(config.PROMPTS_DIR / "friday_chat.md").read_text().strip()]
    spell = config.KNOWLEDGE_DIR / "name-spellings.md"
    if spell.exists():
        parts.append("## Confirmed name spellings\n\n" + spell.read_text().strip())
    return "\n\n".join(parts)


def outline(doc: dict) -> str:
    lines = [f"Subject: {doc['subject']}  ·  {doc['friday']}"]
    for s in doc["sections"]:
        if not s["items"]:
            continue
        lines.append(f"[{s['key']}] " + (s.get("label") or s.get("title") or ""))
        for i, it in enumerate(s["items"]):
            head = it.get("title") or it.get("eyebrow") or (it["body"][0][:50] if it["body"] else "(untitled)")
            lines.append(f"  {i}. {head}" + (f" — {it['when']}" if it.get("when") else "") + (" [photo]" if it.get("image") else "") + (" [button]" if it.get("link") else ""))
    lines.append(f"calendar: {len(doc['calendar'])} lines · birthdays {len(doc['birthdays'])} · anniversaries {len(doc['anniversaries'])} · serve {len(doc['serve'])} lines")
    return "\n".join(lines)


def revise(sunday: str, message: str, *, store: Store, user: str | None = None) -> dict:
    key = chat_key(sunday)
    if store.chat_count(key) >= MAX_MESSAGES:
        raise model.SpendCap(f"This email has used its {MAX_MESSAGES} chat messages; edit the form directly.")
    job = model.Job("chat", f"friday:{sunday}", store)
    job.check()
    store.add_chat(key, "user", message, user)
    doc = friday.load(sunday)
    history = store.chat_history(key, limit=HISTORY_TURNS * 2 + 1)[:-1]
    context = "\n".join(f"{h['role']}: {h['text']}" for h in history[-HISTORY_TURNS * 2:])
    first = (f"The Friday Email for Sunday {sunday}. Outline:\n{outline(doc)}\n\n"
             + (f"Recent conversation:\n{context}\n\n" if context else "") + f"Request: {message}")
    messages: list[dict] = [{"role": "user", "content": first}]
    edits: list[str] = []
    reply = ""
    turns = 0
    while True:
        try:
            resp = model.complete(job, system=_system(), messages=messages, tools=TOOLS, effort="medium", max_tokens=8000)
        except model.SpendCap as e:
            reply = reply or f"Stopped before finishing: {e}"
            break
        turns += 1
        messages.append({"role": "assistant", "content": [b.model_dump(exclude_none=True) for b in resp.content]})
        uses = model.tool_uses(resp)
        if not uses:
            reply = model.text_of(resp).strip() or "Done."
            break
        results = []
        for u in uses:
            try:
                if u.name == "read_email":
                    content = json.dumps(doc, ensure_ascii=False)
                elif u.name == "edit_email":
                    doc, note = apply_edit(doc, u.input["path"], u.input["value"])
                    friday.save(doc)
                    edits.append(note)
                    content = f"OK: {note}. Call render_email when your edits are complete."
                elif u.name == "render_email":
                    friday.build(sunday, doc)
                    content = outline(doc)
                else:
                    raise ValueError(f"unknown tool {u.name}")
                results.append({"type": "tool_result", "tool_use_id": u.id, "content": content})
            except (ValueError, KeyError, IndexError) as e:
                results.append({"type": "tool_result", "tool_use_id": u.id, "content": f"Error: {e}", "is_error": True})
        messages.append({"role": "user", "content": results})
    if edits:
        friday.build(sunday, doc)
    store.add_chat(key, "assistant", reply, None, cost=job.cost)
    store.log("friday.chat", user, json.dumps({"date": sunday, "turns": turns, "cost": round(job.cost, 4), "edits": edits, "reply": reply[:300]}))
    return {"reply": reply, "cost": round(job.cost, 4), "turns": turns, "edits": edits}
