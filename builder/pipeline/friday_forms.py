"""The Friday email's web form ⇄ friday.json."""
from __future__ import annotations

import re
from copy import deepcopy

from builder.pipeline import forms, friday

ITEM_TEXT = ("eyebrow", "title", "when", "note")


def _s(form: dict, key: str) -> str:
    return forms._s(form, key)


def paragraphs(text: str) -> list[str]:
    return [" ".join(p.split()) for p in re.split(r"\n\s*\n", text or "") if p.strip()]


def serve_text(lines: list[dict]) -> str:
    out = []
    for l in lines:
        if l.get("role") is None:
            out.append("- " + l["names"])
        else:
            out.append(f"{l['role']}: {l['names']}" if l["names"] else f"{l['role']}:")
    return "\n".join(out)


def parse_serve(text: str) -> list[dict]:
    out = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("- "):
            out.append({"role": None, "names": line[2:].strip()})
        elif ":" in line:
            role, _, names = line.partition(":")
            out.append({"role": role.strip(), "names": names.strip()})
        else:
            raise ValueError(f"Those Who Serve line needs 'Role: names' or '- sub-line': {line!r}")
    return out


def dated_text(rows: list[dict]) -> str:
    return "\n".join(f"{r['date']}: {', '.join(r['names'])}" for r in rows)


def parse_dated(text: str, what: str) -> list[dict]:
    out = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        m = re.match(r"^(\d{4}-\d{2}-\d{2})\s*:\s*(.+)$", line)
        if not m:
            raise ValueError(f"{what} line must be 'YYYY-MM-DD: names': {line!r}")
        names = [n.strip() for n in re.split(r",(?![^(]*\))", m.group(2)) if n.strip()]
        out.append({"date": m.group(1), "names": names})
    return sorted(out, key=lambda r: r["date"])


def form_to_doc(doc: dict, form: dict, index_map: dict | None = None) -> dict:
    """``index_map`` (optional) is filled with {(si, old_ii): new_ii} for the items kept."""
    d = deepcopy(doc)
    d["subject"] = _s(form, "subject") or "The Friday Email"
    d["preheader"] = _s(form, "preheader") or None
    st, sr = _s(form, "scripture_text"), _s(form, "scripture_ref")
    d["scripture"] = {"text": st, "ref": sr} if st else None
    for si, s in enumerate(d["sections"]):
        s["intro"] = _s(form, f"s{si}_intro") or None
        kept = []
        for ii, it in enumerate(s["items"]):
            p = f"s{si}_i{ii}_"
            if _s(form, p + "remove") == "on":
                continue
            for k in ITEM_TEXT:
                it[k] = _s(form, p + k) or None
            it["when_highlight"] = _s(form, p + "when_highlight") == "on"
            it["body"] = paragraphs(_s(form, p + "body"))
            qt, qr = _s(form, p + "quote_text"), _s(form, p + "quote_ref")
            it["quote"] = {"text": qt, "ref": qr} if qt else None
            ll, lu = _s(form, p + "link_label"), _s(form, p + "link_url")
            if lu and not ll:
                ll = "Learn more"
            it["link"] = {"label": ll, "url": lu} if lu else None
            if _s(form, p + "image_remove") == "on":
                it["image"] = None
            elif it.get("image"):
                it["image"]["alt"] = _s(form, p + "image_alt") or it["image"]["alt"]
                w = _s(form, p + "image_width")
                if w.isdigit():
                    it["image"]["width"] = int(w)
            it["image_position"] = _s(form, p + "image_position") or "after_body"
            if it["body"] or it["title"] or it["quote"] or it["image"] or it["link"]:
                if index_map is not None:
                    index_map[(si, ii)] = len(kept)
                kept.append(it)
        s["items"] = kept
    d["birthdays"] = parse_dated(_s(form, "birthdays"), "Birthdays")
    d["anniversaries"] = parse_dated(_s(form, "anniversaries"), "Anniversaries")
    d["birthdays_note"] = _s(form, "birthdays_note") or None
    d["serve"] = parse_serve(_s(form, "serve"))
    d["calendar"] = forms.parse_calendar_lines(_s(form, "calendar_lines"), d["date"])
    lines = [l.strip() for l in _s(form, "hymn_lines").splitlines() if l.strip()]
    d["closing_hymn"] = {"lines": lines, "attribution": _s(form, "hymn_attribution") or None} if lines else None
    friday.validate(d)
    return d


def blank_item() -> dict:
    return {"eyebrow": None, "title": None, "when": None, "when_highlight": False, "body": [], "note": None,
            "quote": None, "link": None, "image": None, "image_position": "after_body"}


def form_of(doc: dict) -> dict:
    """The form fields the page would post for ``doc`` unchanged (tests, CLI)."""
    f = {"subject": doc["subject"], "preheader": doc.get("preheader") or "",
         "scripture_text": (doc.get("scripture") or {}).get("text", ""), "scripture_ref": (doc.get("scripture") or {}).get("ref", ""),
         "birthdays": dated_text(doc["birthdays"]), "anniversaries": dated_text(doc["anniversaries"]),
         "birthdays_note": doc.get("birthdays_note") or "", "serve": serve_text(doc["serve"]),
         "calendar_lines": forms.calendar_lines_text(doc["calendar"]),
         "hymn_lines": "\n".join((doc.get("closing_hymn") or {}).get("lines", [])),
         "hymn_attribution": (doc.get("closing_hymn") or {}).get("attribution") or ""}
    for si, s in enumerate(doc["sections"]):
        f[f"s{si}_intro"] = s.get("intro") or ""
        for ii, it in enumerate(s["items"]):
            p = f"s{si}_i{ii}_"
            for k in ITEM_TEXT:
                f[p + k] = it.get(k) or ""
            if it.get("when_highlight"):
                f[p + "when_highlight"] = "on"
            f[p + "body"] = "\n\n".join(it["body"])
            f[p + "quote_text"] = (it.get("quote") or {}).get("text", "")
            f[p + "quote_ref"] = (it.get("quote") or {}).get("ref", "")
            f[p + "link_label"] = (it.get("link") or {}).get("label", "")
            f[p + "link_url"] = (it.get("link") or {}).get("url", "")
            f[p + "image_position"] = it.get("image_position") or "after_body"
            if it.get("image"):
                f[p + "image_alt"] = it["image"]["alt"]
                f[p + "image_width"] = str(it["image"].get("width") or "")
    return f
