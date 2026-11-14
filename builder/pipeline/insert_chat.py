"""AI help on the Inserts screen: change an insert by asking, or draft one from pasted text.

One structured-output call per message (``prompts/insert.md``): the model is given
the insert as it stands and the request, and answers with the whole document and a
sentence for the user. The document goes through ``inserts.clean`` like a posted form,
is saved, and the proof is rebuilt. Conversation history is kept in the ``chat`` table
under ``insert:<slug>``; each call is a ``Job`` of kind ``insert`` (``config.JOB_CAPS``).
"""
from __future__ import annotations

import json

from builder import config
from builder.pipeline import inserts, model
from builder.pipeline.store import Store

MAX_MESSAGES = 30       # per insert
HISTORY_TURNS = 4
MAX_SOURCE_CHARS = 6000

_STR = {"type": "string"}
_DOC_KEYS = ["title", "eyebrow", "when", "where", "blurb", "rows", "return_note", "logo", "footer", *inserts.LOOK]
SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["reply", "changed", "doc"],
    "properties": {
        "reply": _STR,
        "changed": {"type": "boolean"},
        "doc": {
            "type": "object", "additionalProperties": False, "required": _DOC_KEYS,
            "properties": {
                "title": _STR, "eyebrow": _STR, "when": _STR, "where": _STR, "blurb": _STR,
                "rows": {"type": "array", "items": {
                    "type": "object", "additionalProperties": False, "required": ["type", "text", "indent"],
                    "properties": {"type": {"type": "string", "enum": list(inserts.ROW_TYPES)}, "text": _STR, "indent": {"type": "boolean"}}}},
                "return_note": _STR, "logo": {"type": "boolean"}, "footer": {"type": "boolean"},
                **{key: {"type": "string", "enum": list(choices)} for key, (_, choices) in inserts.LOOK.items()},
            },
        },
    },
}


def chat_key(slug: str) -> str:
    return f"insert:{slug}"


def _system() -> str:
    parts = [(config.PROMPTS_DIR / "insert.md").read_text().strip()]
    spell = config.KNOWLEDGE_DIR / "name-spellings.md"
    if spell.exists():
        parts.append("## Confirmed name spellings\n\n" + spell.read_text().strip())
    return "\n\n".join(parts)


def for_model(doc: dict) -> dict:
    """The document as the model sees and returns it: the title carries its ``|`` breaks, every key present."""
    out = {**inserts.blank(), **doc}
    out["title"] = out.pop("title_print", None) or out["title"]
    return {k: out[k] for k in _DOC_KEYS}


def _ask(job: model.Job, user_text: str) -> tuple[str, bool, dict | None]:
    resp = model.complete(job, system=_system(), messages=[{"role": "user", "content": user_text}], schema=SCHEMA, effort="low", max_tokens=6000)
    out = model.json_of(resp)
    reply = " ".join(str(out.get("reply") or "").split()) or "Done."
    if not out.get("changed"):
        return reply, False, None
    try:
        return reply, True, inserts.clean(out["doc"])
    except (ValueError, KeyError, TypeError) as e:
        raise model.ModelError(f"The change could not be applied ({e}). Nothing was saved; try wording it another way.") from None


def _fit_note(slug: str, doc: dict) -> str:
    try:
        scale = inserts.build(slug, doc)["scale"]
    except ValueError:
        return " It no longer fits the half-sheet, so there is no proof: ask me to make it fit, or take something out."
    return "" if scale == 1 else f" To fit the half-sheet, everything is printed at {scale:.0%} size."


def revise(slug: str, message: str, *, store: Store, user: str | None = None) -> dict:
    """Apply one request to a saved insert. Returns {"reply", "cost", "changed"}."""
    doc = store.insert_doc(slug)
    if doc is None:
        raise ValueError("no such insert")
    key = chat_key(slug)
    if store.chat_count(key) >= MAX_MESSAGES:
        raise model.SpendCap(f"This insert has used its {MAX_MESSAGES} chat messages; change the form directly.")
    job = model.Job("insert", slug, store)
    job.check()
    history = store.chat_history(key, limit=HISTORY_TURNS * 2)
    store.add_chat(key, "user", message, user)
    context = "\n".join(f"{h['role']}: {h['text']}" for h in history)
    text = ("The insert as it stands:\n```json\n" + json.dumps(for_model(doc), ensure_ascii=False, indent=1) + "\n```\n\n"
            + (f"{inserts.fit_state(slug)}\n\n" if inserts.fit_state(slug) else "")
            + (f"Earlier in this conversation:\n{context}\n\n" if context else "") + f"Request: {message}")
    try:
        reply, changed, new = _ask(job, text)
    except model.ModelError as e:
        store.add_chat(key, "assistant", str(e), None, cost=job.cost)
        raise
    if changed and new != doc:
        store.save_insert(slug, new)
        reply += _fit_note(slug, new)
    else:
        changed = False
    store.add_chat(key, "assistant", reply, None, cost=job.cost)
    store.log("insert.chat", user, json.dumps({"slug": slug, "cost": round(job.cost, 4), "changed": changed, "request": message[:300], "reply": reply[:300]}, ensure_ascii=False))
    return {"reply": reply, "cost": round(job.cost, 4), "changed": changed}


def draft(source: str, *, store: Store, user: str | None = None) -> dict:
    """A new insert drafted from pasted text. Returns {"slug", "reply", "cost"}."""
    source = source.strip()
    if len(source) < 20:
        raise ValueError("paste the announcement or email to draft from")
    job = model.Job("insert", "draft", store)
    job.check()
    text = ("Draft a new sign-up insert from this text. Start from the blank document below and set `changed` to true.\n\n"
            "```json\n" + json.dumps(for_model({**inserts.blank(), "rows": []}), ensure_ascii=False, indent=1) + "\n```\n\n"
            "The text:\n```\n" + source[:MAX_SOURCE_CHARS] + "\n```")
    reply, changed, doc = _ask(job, text)
    if not changed or doc is None:
        raise model.ModelError(reply)
    slug = inserts.new_slug(doc["title"], store)
    store.save_insert(slug, doc)
    reply += _fit_note(slug, doc)
    key = chat_key(slug)
    store.add_chat(key, "user", "Draft this insert from the text I pasted.", user)
    store.add_chat(key, "assistant", reply, None, cost=job.cost)
    store.log("insert.draft", user, json.dumps({"slug": slug, "cost": round(job.cost, 4), "chars": len(source)}))
    return {"slug": slug, "reply": reply, "cost": round(job.cost, 4)}
