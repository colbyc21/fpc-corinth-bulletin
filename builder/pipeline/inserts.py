"""Sign-up inserts made in the app: the Inserts screen's form ↔ a small document,
and that document → the half-sheet HTML, PDF and proof image.

An insert is one landscape letter sheet carrying the same 5.5 x 8.5 in sign-up
form twice, cut down the middle (``templates/insert.html.j2``). The document is
stored in the ``inserts`` table by slug::

    {"title": "...", "eyebrow": "Sign-Up", "when": "...", "where": "...", "blurb": "...",
     "rows": [{"type": "line", "text": "Name(s):", "indent": false}, ...],
     "return_note": "...", "logo": true, "footer": true}

A week lists the slugs it carries in ``week.inserts``; the weekly build renders
each beside the bulletin (``render.render_insert``). The one hand-built insert
that predates this screen (``templates/inserts/thanksgiving-meal.html``) is still
offered on the week page under its own name.
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup, escape

from builder import config
from builder.pipeline import pdf as pdfmod
from builder.pipeline.store import Store

BUILT_IN = {"thanksgiving-meal": "Thanksgiving Meal volunteer insert"}
ROW_TYPES = {
    "line": "Write-in line",
    "short": "Short write-in",
    "check": "Checkbox",
    "checks": "Checkboxes in a row",
    "heading": "Bold text",
    "note": "Small note",
}
DEFAULT_RETURN = "Please place this form in the offering plate or bring it to the church office."
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
RESERVED = {"new", *BUILT_IN}
MAX_ROWS = 24
_PAREN = re.compile(r"(?<=\s)(\([^()]*\))")  # "(required)", not the "(s)" of "Name(s)"


def blank() -> dict:
    return {"title": "", "eyebrow": "Sign-Up", "when": "", "where": "", "blurb": "",
            "rows": [{"type": "line", "text": "Name(s):", "indent": False},
                     {"type": "line", "text": "Phone or email:", "indent": False}],
            "return_note": DEFAULT_RETURN, "logo": True, "footer": True}


def slugify(title: str) -> str:
    s = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:48].strip("-")
    return s or "insert"


def new_slug(title: str, store: Store) -> str:
    base = slugify(title)
    taken = RESERVED | {i["slug"] for i in store.inserts()}
    slug, n = base, 2
    while slug in taken:
        slug, n = f"{base}-{n}", n + 1
    return slug


def clean(doc: dict) -> dict:
    """A document in its stored shape. Raises ValueError with a sentence for the screen."""
    def s(key: str) -> str:
        return " ".join(str(doc.get(key) or "").split())

    out = {"title": s("title"), "eyebrow": s("eyebrow"), "when": s("when"), "where": s("where"),
           "blurb": "\n\n".join(" ".join(p.split()) for p in re.split(r"\n\s*\n", str(doc.get("blurb") or "")) if p.strip()),
           "rows": [], "return_note": s("return_note"), "logo": bool(doc.get("logo")), "footer": bool(doc.get("footer"))}
    if not out["title"]:
        raise ValueError("the insert needs a title")
    for r in doc.get("rows") or []:
        text = " ".join(str(r.get("text") or "").split())
        if not text:
            continue
        if r.get("type") not in ROW_TYPES:
            raise ValueError(f"unknown kind of row: {r.get('type')!r}")
        out["rows"].append({"type": r["type"], "text": text, "indent": bool(r.get("indent"))})
    if len(out["rows"]) > MAX_ROWS:
        raise ValueError(f"too many rows for a half-sheet (the limit is {MAX_ROWS})")
    return out


def form_to_doc(form: dict) -> dict:
    """Posted fields (name → list of values, as the week form passes them) → a cleaned document."""
    def one(key: str) -> str:
        v = form.get(key) or [""]
        return v[0] if isinstance(v, list) else v

    types, texts, indents = form.get("row_type") or [], form.get("row_text") or [], form.get("row_indent") or []
    rows = [{"type": t, "text": x, "indent": i < len(indents) and indents[i] == "1"} for i, (t, x) in enumerate(zip(types, texts))]
    return clean({"title": one("title"), "eyebrow": one("eyebrow"), "when": one("when"), "where": one("where"),
                  "blurb": one("blurb"), "rows": rows, "return_note": one("return_note"),
                  "logo": one("logo") == "on", "footer": one("footer") == "on"})


def from_announcement(ann: dict) -> dict:
    """A starting point from one of a week's announcements: its title and text, and the usual first rows."""
    doc = blank()
    doc["title"] = ann.get("title", "")
    doc["blurb"] = ann.get("body", "")
    return doc


def available(store: Store) -> dict[str, str]:
    """slug → label of every insert a week can carry."""
    return {**{i["slug"]: i["title"] for i in store.inserts()}, **BUILT_IN}


def _paren(text: str) -> Markup:
    """Parentheticals ("(required)", "(9:30 a.m.)") print lighter than the label they follow."""
    parts = _PAREN.split(text)
    return Markup("").join(Markup('<span class="paren">%s</span>') % p if i % 2 else escape(p) for i, p in enumerate(parts))


def _options(text: str) -> list[str]:
    return [o.strip() for o in text.split(",") if o.strip()]


def render(doc: dict) -> str:
    env = Environment(loader=FileSystemLoader(str(config.TEMPLATES_DIR)), autoescape=select_autoescape(default=True),
                      trim_blocks=True, lstrip_blocks=True)
    env.filters["paren"] = _paren
    env.filters["options"] = _options
    return env.get_template("insert.html.j2").render(doc=doc, blurb=[p for p in doc.get("blurb", "").split("\n\n") if p])


def out_dir(slug: str) -> Path:
    return config.VAR / "inserts" / slug


def write_html(doc: dict, path: Path) -> Path:
    """The insert's HTML at ``path``, with the logo it points at beside it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(doc))
    logo = path.parent / "logo-cover.png"
    if not logo.exists():
        logo.write_bytes((config.ASSETS_DIR / "logo-cover.png").read_bytes())
    return path


def to_pdf(renderer: pdfmod.Renderer, html: Path, pdf: Path) -> float:
    """PDF of an insert's HTML, shrunk as far as needed for the form to fit its half-sheet.
    Returns the scale used; raises ValueError when it would not fit even at the floor."""
    return renderer.fitted_pdf(html, pdf, selector=".fit", floor=0.7)


def build(slug: str, doc: dict, renderer: pdfmod.Renderer | None = None) -> dict:
    """HTML, PDF and a proof PNG under ``<var>/inserts/<slug>/``. Returns {"scale": ..., "pdf": ..., "png": ...}."""
    d = out_dir(slug)
    for old in (d / "insert.pdf", d / "proof-1.png"):  # never leave an earlier version's proof standing
        old.unlink(missing_ok=True)
    html = write_html(doc, d / "insert.html")
    own = renderer is None
    r = renderer or pdfmod.Renderer().__enter__()
    try:
        scale = to_pdf(r, html, d / "insert.pdf")
    finally:
        if own:
            r.__exit__(None, None, None)
    png = pdfmod.page_pngs(d / "insert.pdf", d, dpi=110, prefix="proof")[0]
    return {"scale": scale, "pdf": d / "insert.pdf", "png": png}
