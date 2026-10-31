"""Model call 3: the fit loop. Edits ``week.layout`` until no panel overflows.

A manual tool loop (§3.1): the model reads the week, renders and measures,
changes the layout, and measures again, at most ``JOB_CAPS['fit']`` calls
and dollars. It never touches the words. The best layout seen (least total
overflow) is what remains in week.json when the loop ends, even if the cap
was hit. ``build_and_fit`` is the entry point the CLI and the app use.
"""
from __future__ import annotations

import base64
import json
import logging
from copy import deepcopy

from builder import config
from builder.pipeline import build as buildmod, model, pdf as pdfmod
from builder.pipeline import week as weekmod
from builder.pipeline.store import Store

log = logging.getLogger("builder.fit")
LITURGY_PANELS = (3, 4, 5, 6)
PROOF_DPI = 72

_PAGE_STYLE = {"type": ["object", "null"], "additionalProperties": False, "required": ["spread", "row_gap"],
               "properties": {"spread": {"type": ["boolean", "null"]}, "row_gap": {"type": ["string", "null"]}}}
LAYOUT_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["pages", "page_style", "notes", "overrides", "announcement_columns"],
    "properties": {
        "announcement_columns": {"type": ["array", "null"], "items": {"type": "array", "items": {"type": "integer"}},
                                 "description": "Back cover (page 8): two lists of announcement indexes, left column then right. Null splits in half."},
        "pages": {"type": "object", "additionalProperties": False, "required": ["p3", "p4", "p5", "p6"],
                  "properties": {p: {"type": "array", "items": {"type": "string"}} for p in ("p3", "p4", "p5", "p6")}},
        "page_style": {"type": "object", "additionalProperties": False, "required": ["p3", "p4", "p5", "p6"],
                       "properties": {p: _PAGE_STYLE for p in ("p3", "p4", "p5", "p6")}},
        "notes": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["notes", "at", "span", "align"],
            "properties": {"notes": {"type": "array", "items": {"type": "string"}}, "at": {"type": "string"},
                           "span": {"type": "integer"}, "align": {"type": "string", "enum": ["start", "end"]}}}},
        "overrides": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["page", "selector", "css", "reason"],
            "properties": {"page": {"type": ["string", "null"]}, "selector": {"type": "string"}, "css": {"type": "string"},
                           "reason": {"type": ["string", "null"]}}}},
    },
}
TOOLS = [
    {"name": "read_week", "description": "The week's liturgy items (id, type, title, how long the text is), its margin notes (id and length) and the current layout.",
     "strict": True, "input_schema": {"type": "object", "additionalProperties": False, "properties": {}, "required": []}},
    {"name": "set_layout", "description": "Replace the week's layout (pages, page_style, notes, overrides). Validated; an error means nothing changed.",
     "strict": True, "input_schema": {"type": "object", "additionalProperties": False, "required": ["layout"], "properties": {"layout": LAYOUT_SCHEMA}}},
    {"name": "render_and_measure", "description": "Render the booklet with the current layout and return each panel's overflow in CSS pixels plus images of the liturgy panels.",
     "strict": True, "input_schema": {"type": "object", "additionalProperties": False, "properties": {}, "required": []}},
]


def summarize_week(week: dict) -> dict:
    notes = weekmod.notes_by_item(week)
    items = []
    anns = [{"index": i, "title": a["title"], "words": len((a.get("body", "") + " " + (a.get("print_suffix") or "")).split())}
            for i, a in enumerate(week.get("announcements", []))]
    for it in week["liturgy"]["items"]:
        words = len((it.get("text") or "").split()) + sum(len(ln["text"].split()) for ln in it.get("lines", []))
        row = {"id": it["id"], "type": it["type"], "title": it["title"], "words": words}
        if it["id"] in notes:
            row["note_words"] = len(notes[it["id"]]["text"].split())
        items.append(row)
    return {"items": items, "announcements": anns, "layout": week["layout"]}


def total_overflow(measurements: list[dict]) -> float:
    return sum(max(m["overflow_px"], m["grid_overflow_px"], m["content_past_bottom_px"]) for m in measurements)


def _measure_text(report: dict) -> str:
    rows = []
    for m in report["measurements"]:
        worst = max(m["overflow_px"], m["grid_overflow_px"], m["content_past_bottom_px"])
        rows.append(f"{m['label']}: {'OVERFLOW ' if worst > 0.5 else 'ok '}{worst:.1f}px (scroll {m['overflow_px']}, grid {m['grid_overflow_px']}, past bottom {m['content_past_bottom_px']})")
    return "\n".join(rows)


def _panel_images(report: dict) -> list[dict]:
    """The liturgy panels plus any other panel that overflows (the back cover, usually)."""
    wanted = list(LITURGY_PANELS)
    for m in report.get("overflow", []):
        pid = m.get("id", "") if isinstance(m, dict) else ""
        if pid.startswith("page-") and pid[5:].isdigit() and int(pid[5:]) not in wanted:
            wanted.append(int(pid[5:]))
    blocks = []
    for n in sorted(wanted):
        p = report.get("panels", {}).get(str(n))
        if not p:
            continue
        data = base64.standard_b64encode(open(p, "rb").read()).decode()
        blocks.append({"type": "text", "text": f"Page {n}:"})
        blocks.append({"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": data}})
    return blocks


def apply_layout(week: dict, layout: dict) -> dict:
    """Merge a tool-supplied layout into the week and validate. Returns the new week."""
    new = deepcopy(week)
    lay = new["layout"]
    lay["pages"] = {p: list(layout["pages"].get(p, [])) for p in ("p3", "p4", "p5", "p6")}
    lay["page_style"] = {p: {k: v for k, v in (st or {}).items() if v is not None}
                         for p, st in (layout.get("page_style") or {}).items() if st and any(v is not None for v in st.values())}
    lay["notes"] = [{"notes": n["notes"], "at": n["at"], "span": int(n["span"]), "align": n.get("align") or "start"} for n in layout.get("notes", [])]
    lay["overrides"] = [{"page": o.get("page"), "selector": o["selector"], "css": o["css"], "reason": o.get("reason")} for o in layout.get("overrides", [])]
    weekmod.validate(new)
    ids = {it["id"] for it in new["liturgy"]["items"]}
    note_ids = {n["for"] for n in new["liturgy"].get("notes", [])}
    for n in lay["notes"]:
        if n["at"] not in ids:
            raise ValueError(f"notes placement at unknown item {n['at']!r}")
        for nid in n["notes"]:
            if nid not in note_ids:
                raise ValueError(f"no margin note for item {nid!r}")
    cols = layout.get("announcement_columns")
    if cols is not None:
        n = len(new.get("announcements", []))
        flat = [i for c in cols for i in c]
        if len(cols) != 2 or sorted(flat) != list(range(n)):
            raise ValueError(f"announcement_columns must be two lists covering indexes 0..{n - 1} once each")
        lay["announcement_columns"] = [list(c) for c in cols]
    else:
        lay["announcement_columns"] = None
    return new


def fit(date: str, *, store: Store, renderer: pdfmod.Renderer | None = None, report: dict | None = None,
        job: model.Job | None = None) -> dict:
    """Run the fit loop. Returns {report, fitted, job, summary, turns}."""
    job = job or model.Job("fit", date, store)
    week = weekmod.load_week(date)
    report = report or buildmod.build(date, store=store, renderer=renderer, proofs=True, dpi=PROOF_DPI, fix_names=False)
    best = {"overflow": total_overflow(report["measurements"]), "layout": deepcopy(week["layout"]), "report": report}
    if best["overflow"] <= 0.5:
        return {"report": report, "fitted": True, "job": job, "summary": "Already fits.", "turns": 0}

    system = (config.PROMPTS_DIR / "fit.md").read_text().strip()
    first = [{"type": "text", "text": f"Week {date}. The booklet overflows. Current measurements:\n{_measure_text(report)}\n\n"
                                      f"Week summary and layout:\n```json\n{json.dumps(summarize_week(week), ensure_ascii=False)}\n```\n\nPanels:"}]
    first += _panel_images(report)
    messages: list[dict] = [{"role": "user", "content": first}]
    summary = ""
    turns = 0
    while True:
        try:
            resp = model.complete(job, system=system, messages=messages, tools=TOOLS, effort="high", max_tokens=8000)
        except model.SpendCap as e:
            summary = f"Stopped: {e}"
            break
        turns += 1
        messages.append({"role": "assistant", "content": [b.model_dump(exclude_none=True) for b in resp.content]})
        uses = model.tool_uses(resp)
        if not uses:
            summary = model.text_of(resp).strip() or "Done."
            break
        results = []
        for u in uses:
            try:
                if u.name == "read_week":
                    content: list | str = json.dumps(summarize_week(week), ensure_ascii=False)
                elif u.name == "set_layout":
                    week = apply_layout(week, u.input["layout"])
                    weekmod.save_week(week)
                    content = "Layout saved. Call render_and_measure to check it."
                elif u.name == "render_and_measure":
                    report = buildmod.build(date, store=store, renderer=renderer, proofs=True, dpi=PROOF_DPI, fix_names=False)
                    ov = total_overflow(report["measurements"])
                    if ov < best["overflow"]:
                        best = {"overflow": ov, "layout": deepcopy(week["layout"]), "report": report}
                    content = [{"type": "text", "text": _measure_text(report)}] + ([] if ov <= 0.5 else _panel_images(report))
                else:
                    raise ValueError(f"unknown tool {u.name}")
                results.append({"type": "tool_result", "tool_use_id": u.id, "content": content})
            except (ValueError, KeyError) as e:
                results.append({"type": "tool_result", "tool_use_id": u.id, "content": f"Error: {e}", "is_error": True})
        messages.append({"role": "user", "content": results})
    # leave the best layout on disk
    if json.dumps(week["layout"], sort_keys=True) != json.dumps(best["layout"], sort_keys=True):
        week["layout"] = best["layout"]
        weekmod.save_week(week)
        report = buildmod.build(date, store=store, renderer=renderer, proofs=True, fix_names=False)
    elif report is not best["report"] or report.get("panels") is None or PROOF_DPI != 110:
        report = buildmod.build(date, store=store, renderer=renderer, proofs=True, fix_names=False)
    fitted = total_overflow(report["measurements"]) <= 0.5
    store.log("fit", detail=json.dumps({"date": date, "fitted": fitted, "turns": turns, "cost": round(job.cost, 4), "summary": summary[:300]}))
    return {"report": report, "fitted": fitted, "job": job, "summary": summary, "turns": turns}


def build_and_fit(date: str, *, store: Store, renderer: pdfmod.Renderer | None = None) -> dict:
    """Deterministic build; if a panel overflows and model calls are allowed, run the fit loop."""
    own = renderer is None
    r = renderer or pdfmod.Renderer().__enter__()
    try:
        report = buildmod.build(date, store=store, renderer=r)
        result = {"report": report, "fitted": not report["overflow"], "fit": None}
        if report["overflow"] and config.MODEL_CALLS:
            if store.builds_this_week(date) >= config.BUILDS_PER_WEEK:
                result["fit"] = {"skipped": f"The fit loop has already run {config.BUILDS_PER_WEEK} times for this Sunday; adjust the layout by hand."}
                return result
            res = fit(date, store=store, renderer=r, report=report)
            result.update(report=res["report"], fitted=res["fitted"],
                          fit={"turns": res["turns"], "cost": round(res["job"].cost, 4), "summary": res["summary"], "job": res["job"].id})
        return result
    finally:
        if own:
            r.__exit__(None, None, None)
