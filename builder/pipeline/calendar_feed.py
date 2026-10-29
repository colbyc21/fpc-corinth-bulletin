"""Church calendar: the back-cover box, the public ICS feed and the /calendar/ page data.

The events table is the source of truth (decision §11.3 item 6). Weekly
recurrences are a small rule string ``WEEKLY;BYDAY=TU`` with an optional
``until_date``; that is all the church calendar has needed. The ICS emits
the same rule as RRULE so subscribers see one repeating event, not copies.
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


def _rule(recurrence: str | None) -> tuple[str, list[int]] | None:
    if not recurrence:
        return None
    parts = dict(p.split("=", 1) if "=" in p else (p, "") for p in recurrence.split(";"))
    freq = next(iter(parts)) if parts else ""
    if freq != "WEEKLY":
        return None
    days = [WEEKDAY_CODES[d] for d in parts.get("BYDAY", "").split(",") if d in WEEKDAY_CODES]
    return "WEEKLY", days


def occurrences(event: dict, start: date, end: date) -> list[date]:
    """Dates on which the event occurs within [start, end]."""
    first = date.fromisoformat(event["start_date"])
    last = date.fromisoformat(event["end_date"]) if event.get("end_date") else first
    rule = _rule(event.get("recurrence"))
    if not rule:
        if last < start or first > end:
            return []
        return [first]  # multi-day one-offs list once, on the first day
    _, days = rule
    until = date.fromisoformat(event["until_date"]) if event.get("until_date") else None
    out = []
    d = max(start, first)
    while d <= end:
        if (until is None or d <= until) and d.weekday() in (days or [first.weekday()]):
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
            r: dict = {"freq": "weekly"}
            if rule[1]:
                r["byday"] = [k for k, v in WEEKDAY_CODES.items() if v in rule[1]]
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
