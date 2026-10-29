"""Name spellings the church has confirmed (docs/knowledge/name-spellings.md).

The content form and past bulletins disagree on these; the confirmed form
always wins, even over a prior bulletin. ``fix_text`` is applied to every
free-text field before rendering and returns the list of changes so the
proof page can show them.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# (pattern, replacement, why). Patterns are case-sensitive on purpose: a
# misspelling in a name is usually a specific wrong form, not a casing issue.
RULES: list[tuple[str, str, str]] = [
    (r"\bLauren Porter\b", "Laurin Porter", "pastor's spouse is Laurin (confirmed 2026-07-30)"),
    (r"\bLaurin and Waring Porter\b", "Waring and Laurin Porter", "lead with the pastor"),
    (r"\bMalachi Dor[ae]n\b", "Malechi Doren", "accompanist is Malechi Doren (confirmed 2026-08-26)"),
    (r"\bMalechi Doran\b", "Malechi Doren", "accompanist is Malechi Doren (confirmed 2026-08-26)"),
    (r"\bMcclamroch\b", "McClamroch", "confirmed 2026-08-26"),
    (r"\bDr\. Stephen Blair\b", "Dr. Steve Blair", "use Steve, not Stephen (confirmed 2026-10-01)"),
    (r"\bStephen Blair\b", "Steve Blair", "use Steve, not Stephen (confirmed 2026-10-01)"),
    (r"\bGiaccaligini\b", "Giaccaglini", "content form misspelling (2026-10-04)"),
    (r"\bLegoff\b", "LeGoff", "capital G (confirmed 2026-10-29)"),
    (r"\bJoy ?Riders?\b", "Joy Riders", "seniors' group is 'Joy Riders', two words"),
]

_COMPILED = [(re.compile(p), r, why) for p, r, why in RULES]


@dataclass
class Change:
    where: str
    before: str
    after: str
    reason: str

    def as_dict(self) -> dict:
        return {"where": self.where, "before": self.before, "after": self.after, "reason": self.reason}


def fix_text(text: str | None, where: str = "") -> tuple[str | None, list[Change]]:
    if not text:
        return text, []
    changes: list[Change] = []
    out = text
    for rx, repl, why in _COMPILED:
        def _sub(m: re.Match, repl=repl, why=why) -> str:
            if m.group(0) != repl:
                changes.append(Change(where, m.group(0), repl, why))
            return repl
        out = rx.sub(_sub, out)
    return out, changes


def fix_week(week: dict) -> list[dict]:
    """Apply the rules in place to the fields that carry names. Returns changes."""
    changes: list[Change] = []

    def fix(container, key, where):
        val = container.get(key)
        if isinstance(val, str):
            new, ch = fix_text(val, where)
            container[key] = new
            changes.extend(ch)

    for role in list(week.get("serve", {})):
        fix(week["serve"], role, f"serve.{role}")
    for i, a in enumerate(week.get("announcements", [])):
        for k in ("title", "body", "detail", "print_suffix"):
            fix(a, k, f"announcements[{i}].{k}")
    if week.get("flowers"):
        for k in ("given", "message", "signed"):
            fix(week["flowers"], k, f"flowers.{k}")
    for i, c in enumerate(week.get("calendar", [])):
        for j, _ in enumerate(c.get("items", [])):
            new, ch = fix_text(c["items"][j], f"calendar[{i}].items[{j}]")
            c["items"][j] = new
            changes.extend(ch)
    for it in week["liturgy"]["items"]:
        for k in ("name", "credit", "text"):
            fix(it, k, f"liturgy.{it['id']}.{k}")
    for k in ("preacher",):
        fix(week["sermon"], k, f"sermon.{k}")
    if week.get("staff"):
        for i, s in enumerate(week["staff"]):
            fix(s, "name", f"staff[{i}].name")
    return [c.as_dict() for c in changes]
