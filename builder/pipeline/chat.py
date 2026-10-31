"""Model call 4: the chat box on the proof page.

One message = one job (``JOB_CAPS['chat']``: 4 calls, $1). The model has
the fit loop's tools plus ``edit_week`` (one value by JSON pointer) and must
render after editing. The exchange is kept in the ``chat`` table so the
volunteer sees the thread and the next message gets the recent context.
"""
from __future__ import annotations

import json
import logging
from copy import deepcopy

from builder import config
from builder.pipeline import build as buildmod, fit as fitmod, model, names, pdf as pdfmod
from builder.pipeline import week as weekmod
from builder.pipeline.store import Store

log = logging.getLogger("builder.chat")
MAX_MESSAGES_PER_WEEK = 30
HISTORY_TURNS = 6

TOOLS = [
    {"name": "read_week", "description": "The whole week.json for this Sunday.",
     "strict": True, "input_schema": {"type": "object", "additionalProperties": False, "properties": {}, "required": []}},
    {"name": "edit_week", "description": "Set one value in week.json by JSON pointer path. value is JSON text; null clears; \"__delete__\" removes an array element or key; a path ending in /- appends to an array.",
     "strict": True, "input_schema": {"type": "object", "additionalProperties": False, "required": ["path", "value"],
                                      "properties": {"path": {"type": "string"}, "value": {"type": "string"}}}},
    fitmod.TOOLS[1],
    fitmod.TOOLS[2],
]
PROTECTED = ("/date", "/copyedit", "/layout")


def pointer_set(doc: dict, path: str, value, delete: bool = False) -> None:
    if not path.startswith("/"):
        raise ValueError("path must start with /")
    parts = [p.replace("~1", "/").replace("~0", "~") for p in path[1:].split("/")]
    cur = doc
    for p in parts[:-1]:
        if isinstance(cur, list):
            cur = cur[int(p)]
        elif isinstance(cur, dict):
            if p not in cur:
                cur[p] = {}
            cur = cur[p]
        else:
            raise ValueError(f"cannot descend into {p!r}")
    last = parts[-1]
    if isinstance(cur, list):
        if last == "-":
            if delete:
                raise ValueError("cannot delete '-'")
            cur.append(value)
        else:
            i = int(last)
            if delete:
                del cur[i]
            else:
                cur[i] = value
    elif isinstance(cur, dict):
        if delete:
            cur.pop(last, None)
        else:
            cur[last] = value
    else:
        raise ValueError(f"cannot set {last!r} on a {type(cur).__name__}")


def apply_edit(week: dict, path: str, value_text: str) -> tuple[dict, str]:
    for p in PROTECTED:
        if path == p or path.startswith(p + "/"):
            raise ValueError(f"{p} is not editable here" + (" (use set_layout)" if p == "/layout" else ""))
    try:
        value = json.loads(value_text)
    except json.JSONDecodeError:
        value = value_text  # a bare string is fine
    new = deepcopy(week)
    delete = value == "__delete__"
    pointer_set(new, path, None if delete else value, delete=delete)
    new = weekmod.with_defaults(new)
    changes = names.fix_week(new)
    weekmod.validate(new)
    note = f"set {path}" + (f"; name rule applied: {changes[0]['before']} → {changes[0]['after']}" if changes else "")
    return new, note


def _system(store: Store) -> str:
    parts = [(config.PROMPTS_DIR / "chat.md").read_text().strip()]
    spell = config.KNOWLEDGE_DIR / "name-spellings.md"
    if spell.exists():
        parts.append("## Confirmed name spellings\n\n" + spell.read_text().strip())
    return "\n\n".join(parts)


def revise(date: str, message: str, *, store: Store, user: str | None = None, renderer: pdfmod.Renderer | None = None) -> dict:
    """Handle one chat message. Returns {reply, cost, turns, report, edits}."""
    if store.chat_count(date) >= MAX_MESSAGES_PER_WEEK:
        raise model.SpendCap(f"This bulletin has used its {MAX_MESSAGES_PER_WEEK} chat messages; edit the form or the JSON directly.")
    job = model.Job("chat", date, store)
    job.check()
    store.add_chat(date, "user", message, user)
    week = weekmod.load_week(date)
    history = store.chat_history(date, limit=HISTORY_TURNS * 2 + 1)[:-1]
    context = "\n".join(f"{h['role']}: {h['text']}" for h in history[-HISTORY_TURNS * 2:])
    report = buildmod.last_report(date)
    status = fitmod._measure_text(report) if report else "not built yet"
    first = (f"Week {date}. Current panel measurements:\n{status}\n\n"
             + (f"Recent conversation:\n{context}\n\n" if context else "")
             + f"Request: {message}")
    messages: list[dict] = [{"role": "user", "content": first}]
    own = renderer is None
    r = renderer or pdfmod.Renderer().__enter__()
    edits: list[str] = []
    reply = ""
    turns = 0
    try:
        while True:
            try:
                resp = model.complete(job, system=_system(store), messages=messages, tools=TOOLS, effort="medium", max_tokens=8000)
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
                    if u.name == "read_week":
                        content: list | str = json.dumps(week, ensure_ascii=False)
                    elif u.name == "edit_week":
                        week, note = apply_edit(week, u.input["path"], u.input["value"])
                        weekmod.save_week(week)
                        edits.append(note)
                        content = f"OK: {note}. Call render_and_measure when your edits are complete."
                    elif u.name == "set_layout":
                        week = fitmod.apply_layout(week, u.input["layout"])
                        weekmod.save_week(week)
                        edits.append("layout changed")
                        content = "Layout saved. Call render_and_measure to check it."
                    elif u.name == "render_and_measure":
                        report = buildmod.build(date, store=store, renderer=r, proofs=True, fix_names=True)
                        week = weekmod.load_week(date)
                        ov = fitmod.total_overflow(report["measurements"])
                        content = [{"type": "text", "text": fitmod._measure_text(report)}] + ([] if ov <= 0.5 else fitmod._panel_images(report))
                    else:
                        raise ValueError(f"unknown tool {u.name}")
                    results.append({"type": "tool_result", "tool_use_id": u.id, "content": content})
                except (ValueError, KeyError, IndexError) as e:
                    results.append({"type": "tool_result", "tool_use_id": u.id, "content": f"Error: {e}", "is_error": True})
            messages.append({"role": "user", "content": results})
    finally:
        if own:
            r.__exit__(None, None, None)
    store.add_chat(date, "assistant", reply, None, cost=job.cost)
    store.log("chat", user, json.dumps({"date": date, "turns": turns, "cost": round(job.cost, 4), "edits": edits, "reply": reply[:300]}))
    return {"reply": reply, "cost": round(job.cost, 4), "turns": turns, "report": report, "edits": edits}
