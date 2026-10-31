"""Render week.json into the print booklet and the scroll page.

Two Jinja2 templates, built from the 2026-11-01 files:

* ``print-legal.html.j2`` - 14x8.5 in legal landscape, two sheets, eight
  panels. The liturgy pages are CSS grids with a notes column; which items
  go on which page, and where the margin notes sit, is data in
  ``week.layout`` (see :func:`plan_pages`). Nothing here guesses.
* ``scroll.html.j2`` - the mobile page published at /scroll/<date>/.

Both templates receive the same context from :func:`context`.
"""
from __future__ import annotations

import math
import time
from pathlib import Path

from jinja2 import ChainableUndefined, Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup

from builder import config
from builder.pipeline import dates, week as weekmod
from builder.pipeline.store import ROLE_LABELS, Store

PRINT_SERVE_COLUMNS = (
    ["assisting_elder", "greeters", "acolytes", "circle_of_prayer", "childrens_church", "av_engineer"],
    ["ushers", "nursery"],
)
SCROLL_SERVE_ORDER = ["assisting_elder", "ushers", "greeters", "nursery", "acolytes", "childrens_church", "circle_of_prayer", "av_engineer"]
PAGE_SIDE = {"p3": "notes-right", "p4": "notes-left", "p5": "notes-right", "p6": "notes-left"}


def _env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(config.TEMPLATES_DIR)),
        autoescape=select_autoescape(default=True, default_for_string=True),
        undefined=ChainableUndefined,  # optional keys render empty
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )
    env.filters["q"] = lambda s: Markup("&ldquo;") + (s or "") + Markup("&rdquo;")
    env.filters["long_date"] = dates.long_date
    env.filters["month_day"] = dates.month_day
    return env


def plan_pages(week: dict) -> dict[str, list[dict]]:
    """Turn layout.pages + layout.notes into a list of grid cells per page.

    Cell kinds: ``section`` (full-width heading), ``main`` (a worship item),
    ``note`` (a notes-column cell holding one or more notes, possibly
    spanning several rows) and ``empty`` (the empty notes cell that keeps
    the grid aligned). A note placed by ``layout.notes`` never also renders
    beside its own item.
    """
    items = weekmod.items_by_id(week)
    notes = weekmod.notes_by_item(week)
    placements = {p["at"]: p for p in week["layout"]["notes"]}
    placed_elsewhere = {n for p in week["layout"]["notes"] for n in p["notes"]}
    pages: dict[str, list[dict]] = {}
    for page, ids in week["layout"]["pages"].items():
        cells: list[dict] = []
        skip = 0
        for iid in ids:
            it = items[iid]
            if it["type"] == "section":
                if not it.get("scroll_only"):
                    cells.append({"kind": "section", "item": it})
                    skip = max(0, skip - 1)
                continue
            cells.append({"kind": "main", "item": it})
            if iid in placements:
                p = placements[iid]
                cells.append({"kind": "note", "notes": [notes[n] for n in p["notes"]], "span": p["span"], "align": p.get("align", "start")})
                skip = p["span"] - 1
            elif skip > 0:
                skip -= 1
            elif iid in notes and iid not in placed_elsewhere:
                cells.append({"kind": "note", "notes": [notes[iid]], "span": 1, "align": None})
            else:
                cells.append({"kind": "empty"})
        pages[page] = cells
    return pages


def scroll_sections(week: dict) -> list[dict]:
    notes = weekmod.notes_by_item(week)
    sections: list[dict] = []
    current = {"title": None, "items": []}
    for it in week["liturgy"]["items"]:
        if it["type"] == "section":
            if current["items"] or current["title"]:
                sections.append(current)
            current = {"title": it["title"], "items": []}
            continue
        current["items"].append({"item": it, "note": notes.get(it["id"])})
    sections.append(current)
    return sections


def overrides_css(week: dict) -> str:
    rules = []
    for o in week["layout"].get("overrides", []):
        prefix = f"#page-{o['page'][1:]} " if o.get("page") else ""
        rules.append(f"{prefix}{o['selector']} {{ {o['css']} }}")
    return "\n".join(rules)


def context(week: dict, store: Store | None = None) -> dict:
    """Everything both templates need, computed once."""
    store = store or Store()
    standing_welcome = store.standing("welcome") or {}
    standing_liturgy = store.standing("liturgy") or {}
    staff = week.get("staff") or store.standing("staff") or []

    variant = week.get("welcome_variant", "regular")
    if variant == "custom":
        children = week.get("welcome_custom") or []
    else:
        children = (standing_welcome.get("children") or {}).get(variant) or []

    anns = week.get("announcements", [])
    cols = week["layout"].get("announcement_columns")
    if cols:
        ann_cols = [[anns[i] for i in col if i < len(anns)] for col in cols]
    else:
        half = math.ceil(len(anns) / 2)
        ann_cols = [anns[:half], anns[half:]]

    serve = week.get("serve", {})
    serve_print = [[(ROLE_LABELS[r], serve.get(r)) for r in col if serve.get(r)] for col in PRINT_SERVE_COLUMNS]
    serve_scroll = [(ROLE_LABELS[r], serve.get(r)) for r in SCROLL_SERVE_ORDER if serve.get(r)]

    calendar = [{"label": c.get("label") or dates.month_day(c["date"]), "text": " · ".join(c["items"])} for c in week.get("calendar", [])]

    sermon = week["sermon"]
    points = []
    for p in sermon.get("points", []):
        points.append({**p, "lines": p.get("lines") or sermon.get("notes_lines_per_point", 3)})

    page_style = week["layout"].get("page_style", {})
    pages_meta = {}
    for p in ("p3", "p4", "p5", "p6"):
        st = page_style.get(p, {})
        pages_meta[p] = {
            "classes": " ".join(["liturgy-grid", PAGE_SIDE[p]] + (["spread"] if st.get("spread", True) else [])),
            "style": f"row-gap:{st['row_gap']};" if st.get("row_gap") else "",
        }

    return {
        "week": week,
        "date_long": dates.long_date(week["date"]),
        "subtitle": weekmod.subtitle(week),
        "welcome": standing_welcome,
        "children_paragraphs": children,
        "lit": standing_liturgy,
        "liturgy_intro": week["liturgy"].get("intro") or standing_liturgy.get("intro", ""),
        "staff": staff,
        "staff_cols": (staff[0::2], staff[1::2]),
        "announcement_cols": ann_cols,
        "serve_print": serve_print,
        "serve_scroll": serve_scroll,
        "calendar": calendar,
        "sermon": sermon,
        "sermon_points": points,
        "pages": plan_pages(week),
        "pages_meta": pages_meta,
        "scroll_sections": scroll_sections(week),
        "overrides_css": overrides_css(week),
        "communion": week["special"].get("communion", False),
        "flowers": week.get("flowers"),
        "site_url": config.SITE_URL,
        "build_id": time.strftime("%Y%m%dT%H%M%S"),
    }


def render_print(week: dict, store: Store | None = None) -> str:
    return _env().get_template("print-legal.html.j2").render(**context(week, store))


def render_scroll(week: dict, store: Store | None = None) -> str:
    return _env().get_template("scroll.html.j2").render(**context(week, store))


def render_insert(name: str, week: dict) -> str | None:
    path = config.TEMPLATES_DIR / "inserts" / f"{name}.html"
    if not path.exists():
        return None
    html = path.read_text()
    return html.replace("October 25, 2026", dates.long_date(week["date"]), 1)


def write_outputs(week: dict, store: Store | None = None, out_dir: Path | None = None) -> dict[str, Path]:
    """Write print HTML, scroll HTML, logo asset and any inserts for the week."""
    date = week["date"]
    wdir = out_dir or config.week_dir(date)
    wdir.mkdir(parents=True, exist_ok=True)
    out = {}
    print_path = wdir / f"bulletin-{date}-legal.html"
    print_path.write_text(render_print(week, store))
    out["print_html"] = print_path
    logo = wdir / "logo-cover.png"
    if not logo.exists():
        logo.write_bytes((config.ASSETS_DIR / "logo-cover.png").read_bytes())
    out["logo"] = logo
    scroll_path = (config.SCROLL_DIR if out_dir is None else wdir) / f"{date}.html"
    scroll_path.parent.mkdir(parents=True, exist_ok=True)
    scroll_path.write_text(render_scroll(week, store))
    out["scroll_html"] = scroll_path
    for name in week.get("inserts", []):
        html = render_insert(name, week)
        if html:
            p = wdir / f"{name}-insert.html"
            p.write_text(html)
            out[f"insert:{name}"] = p
    return out
