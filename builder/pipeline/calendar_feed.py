"""Church calendar: the back-cover box, the public ICS feed and the /calendar/ page data.

The events table is the source of truth (decision §11.3 item 6). Repeats
are a small RFC 5545 rule string, ``WEEKLY;BYDAY=TU`` or ``MONTHLY;BYDAY=1SU``
(the first Sunday; ``-1`` is the last), with an optional ``until_date``. The
ICS emits the same rule as RRULE so subscribers see one repeating event, not copies.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from icalendar import Calendar, Event, vRecur

from builder import config
from builder.pipeline import dates
from builder.pipeline.store import Store

WEEKDAY_CODES = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}


ORDINALS = {"1": "first", "2": "second", "3": "third", "4": "fourth", "5": "fifth", "-1": "last"}


def _rule(recurrence: str | None) -> tuple[str, list] | None:
    """``WEEKLY;BYDAY=TU`` -> ("WEEKLY", [1]); ``MONTHLY;BYDAY=1SU`` -> ("MONTHLY", [(1, 6)]).
    A monthly ordinal of -1 means the last such weekday of the month."""
    if not recurrence:
        return None
    parts = dict(p.split("=", 1) if "=" in p else (p, "") for p in recurrence.split(";"))
    freq = next(iter(parts)) if parts else ""
    codes = [d for d in parts.get("BYDAY", "").split(",") if d]
    if freq == "WEEKLY":
        return "WEEKLY", [WEEKDAY_CODES[d] for d in codes if d in WEEKDAY_CODES]
    if freq == "MONTHLY":
        out = []
        for code in codes:
            n, day = code[:-2], code[-2:]
            if day in WEEKDAY_CODES and n in ORDINALS:
                out.append((int(n), WEEKDAY_CODES[day]))
        return "MONTHLY", out
    return None


def recurrence_parts(recurrence: str | None) -> dict:
    """What the form needs to redisplay a rule: freq, ordinal ("1".."5", "-1" or ""), weekday code."""
    rule = _rule(recurrence)
    if not rule or not rule[1]:
        return {"freq": "", "nth": "", "day": ""}
    if rule[0] == "WEEKLY":
        return {"freq": "WEEKLY", "nth": "", "day": _code(rule[1][0])}
    n, wd = rule[1][0]
    return {"freq": "MONTHLY", "nth": str(n), "day": _code(wd)}


def make_recurrence(freq: str, nth: str, day: str) -> str | None:
    """Inverse of recurrence_parts, for the form. Raises ValueError on a bad combination."""
    if not freq:
        return None
    if day not in WEEKDAY_CODES:
        raise ValueError("a repeating event needs a weekday")
    if freq == "WEEKLY":
        return f"WEEKLY;BYDAY={day}"
    if freq == "MONTHLY":
        if nth not in ORDINALS:
            raise ValueError("a monthly event needs which week of the month (first to fifth, or last)")
        return f"MONTHLY;BYDAY={nth}{day}"
    raise ValueError("unknown repeat")


def describe_recurrence(recurrence: str | None) -> str:
    rule = _rule(recurrence)
    if not rule or not rule[1]:
        return ""
    if rule[0] == "WEEKLY":
        return "every " + " and ".join(_name(d) for d in rule[1])
    return " and ".join(f"the {ORDINALS[str(n)]} {_name(d)}" for n, d in rule[1]) + " of the month"


def _code(weekday: int) -> str:
    return next(k for k, v in WEEKDAY_CODES.items() if v == weekday)


def _name(weekday: int) -> str:
    return ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"][weekday]


def _nth_weekday(d: date) -> tuple[int, int]:
    """(ordinal from the start of the month, ordinal from the end as a negative)."""
    from calendar import monthrange

    return (d.day - 1) // 7 + 1, -((monthrange(d.year, d.month)[1] - d.day) // 7 + 1)


def occurrences(event: dict, start: date, end: date) -> list[date]:
    """Dates on which the event occurs within [start, end]."""
    first = date.fromisoformat(event["start_date"])
    last = date.fromisoformat(event["end_date"]) if event.get("end_date") else first
    rule = _rule(event.get("recurrence"))
    if not rule:
        if last < start or first > end:
            return []
        return [first]  # multi-day one-offs list once, on the first day
    freq, spec = rule
    until = date.fromisoformat(event["until_date"]) if event.get("until_date") else None
    out = []
    d = max(start, first)
    while d <= end:
        if until is not None and d > until:
            break
        if freq == "WEEKLY":
            hit = d.weekday() in (spec or [first.weekday()])
        else:
            from_start, from_end = _nth_weekday(d)
            hit = any(d.weekday() == wd and (n == from_start or (n == -1 and from_end == -1)) for n, wd in spec)
        if hit:
            out.append(d)
        d += timedelta(days=1)
    return out


def line_text(event: dict) -> str:
    text = event["title"]
    if event.get("time_text"):
        text += f", {event['time_text']}"
    return text


def range_label(event: dict) -> str | None:
    if event.get("end_date") and event["end_date"] != event["start_date"]:
        a = date.fromisoformat(event["start_date"])
        b = date.fromisoformat(event["end_date"])
        if a.month == b.month:
            return f"({a.strftime('%b')}. {a.day}–{b.day}) "
        return f"({a.strftime('%b')}. {a.day} – {b.strftime('%b')}. {b.day}) "
    return None


def week_calendar(store: Store, sunday: str, horizon_days: int = 7) -> list[dict]:
    """Back-cover lines for the bulletin dated ``sunday``: that Sunday through
    the following Sunday, plus featured events further out."""
    start = date.fromisoformat(sunday)
    end = start + timedelta(days=horizon_days)
    far = start + timedelta(days=120)
    by_date: dict[date, list[str]] = {}
    for ev in store.events():
        window_end = far if ev.get("featured") else end
        for d in occurrences(ev, start, window_end):
            text = (range_label(ev) or "") + line_text(ev)
            items = by_date.setdefault(d, [])
            if text.lower() not in (t.lower() for t in items):
                items.append(text)
    return [{"date": d.isoformat(), "items": items} for d, items in sorted(by_date.items())]


def upcoming(store: Store, days: int = 60, today: date | None = None) -> list[dict]:
    """Flat list for the public /calendar/ page."""
    today = today or date.today()
    end = today + timedelta(days=days)
    rows = []
    for ev in store.events():
        for d in occurrences(ev, today, end):
            rows.append({
                "date": d.isoformat(),
                "label": dates.month_day(d),
                "month": d.strftime("%B %Y"),
                "weekday": d.strftime("%A"),
                "title": ev["title"],
                "time": ev.get("time_text") or "",
                "location": ev.get("location") or "",
                "recurring": bool(ev.get("recurrence")),
            })
    rows.sort(key=lambda r: (r["date"], r["time"] == "", r["time"]))
    return rows


def build_ics(store: Store) -> bytes:
    cal = Calendar()
    cal.add("prodid", "-//First Presbyterian Church of Corinth//Bulletin Builder//EN")
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")
    cal.add("method", "PUBLISH")
    cal.add("x-wr-calname", "FPC Corinth Church Calendar")
    cal.add("x-wr-timezone", "America/Chicago")
    cal.add("refresh-interval;value=duration", "P1D")
    stamp = datetime.now(timezone.utc)
    for ev in store.events():
        e = Event()
        e.add("uid", ev["uid"])
        e.add("dtstamp", stamp)
        e.add("summary", line_text(ev) if ev.get("time_text") else ev["title"])
        start = date.fromisoformat(ev["start_date"])
        e.add("dtstart", start)
        end = date.fromisoformat(ev["end_date"]) if ev.get("end_date") else start
        e.add("dtend", end + timedelta(days=1))  # all-day events: DTEND is exclusive
        if ev.get("location"):
            e.add("location", ev["location"])
        desc = " ".join(x for x in [ev.get("time_text"), ev.get("notes")] if x)
        if desc:
            e.add("description", desc)
        rule = _rule(ev.get("recurrence"))
        if rule:
            r: dict = {"freq": rule[0].lower()}
            if rule[0] == "WEEKLY" and rule[1]:
                r["byday"] = [_code(d) for d in rule[1]]
            elif rule[0] == "MONTHLY":
                r["byday"] = [f"{n}{_code(d)}" for n, d in rule[1]]
            if ev.get("until_date"):
                r["until"] = date.fromisoformat(ev["until_date"])
            e.add("rrule", vRecur(r))
        e.add("last-modified", datetime.fromisoformat(ev["updated_at"]))
        cal.add_component(e)
    return cal.to_ical()


def write_site_files(store: Store, repo: Path | None = None) -> list[Path]:
    """Write src/calendar.ics and src/_data/calendarEvents.json for Eleventy."""
    repo = repo or config.REPO
    ics_path = repo / "src/calendar.ics"
    data_path = repo / "src/_data/calendarEvents.json"
    ics_path.write_bytes(build_ics(store))
    payload = {
        "generated": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "subscribe_url": f"{config.SITE_URL}/calendar.ics",
        "events": upcoming(store, 60),
    }
    data_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return [ics_path, data_path]


def validate_ics(data: bytes) -> int:
    """Round-trip through icalendar; returns the number of VEVENTs."""
    cal = Calendar.from_ical(data)
    return sum(1 for c in cal.walk() if c.name == "VEVENT")
