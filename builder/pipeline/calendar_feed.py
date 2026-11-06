"""Church calendar: the back-cover box, the public ICS feed and the /calendar/ page data.

The events table is the source of truth (decision §11.3 item 6). Repeats
are a small RFC 5545 rule string, ``WEEKLY;BYDAY=TU`` or ``MONTHLY;BYDAY=1SU``
(the first Sunday; ``-1`` is the last), with an optional ``until_date``. The
ICS emits the same rule as RRULE so subscribers see one repeating event, not copies.

One date of a repeating event can be skipped, or moved to another day, time
or place, without touching the rule (the ``event_exceptions`` table).
``instances`` is the one place that applies them; the back-cover box, the
page data and the on-screen calendar all read it, and the ICS carries a skip
as EXDATE and a move as an override VEVENT with RECURRENCE-ID.
"""
from __future__ import annotations

import calendar as _cal
import hashlib
import json
import logging
import re
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

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


def rule_dates(event: dict, start: date, end: date) -> list[date]:
    """Dates the event's own rule lands on within [start, end], before any date is skipped or moved."""
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


def exceptions(event: dict) -> dict[date, dict]:
    """The event's skipped and moved dates, keyed by the date the rule gave them.
    One left behind by a later change to the rule (a Tuesday skip on what is
    now a Wednesday event) no longer applies and is left out."""
    if not _rule(event.get("recurrence")):
        return {}
    out = {}
    for x in event.get("exceptions") or []:
        d = date.fromisoformat(x["occurrence_date"])
        if rule_dates(event, d, d):
            out[d] = x
    return out


def instances(event: dict, start: date, end: date, with_gaps: bool = False) -> list[dict]:
    """What happens within [start, end]: the rule's dates less the skipped
    ones, with each moved one on its new date at its new time and place.
    ``status`` is "" or "moved". ``with_gaps`` adds the dates left empty, as
    "skipped" or "moved_away", for the on-screen calendar. ``original`` is
    always the date the rule gave."""
    exc = exceptions(event)
    out = []
    for d in rule_dates(event, start, end):
        x = exc.get(d)
        if x is None:
            out.append({"date": d, "original": d, "status": "", "exception": None,
                        "time_text": event.get("time_text"), "location": event.get("location")})
        elif with_gaps and (x["kind"] == "skip" or x["new_date"] != x["occurrence_date"]):
            out.append({"date": d, "original": d, "status": "skipped" if x["kind"] == "skip" else "moved_away", "exception": x,
                        "time_text": event.get("time_text"), "location": event.get("location")})
    for d, x in exc.items():
        if x["kind"] != "move":
            continue
        new = date.fromisoformat(x["new_date"])
        if start <= new <= end:
            out.append({"date": new, "original": d, "status": "moved", "exception": x,
                        "time_text": x.get("time_text") or event.get("time_text"),
                        "location": x.get("location") or event.get("location")})
    out.sort(key=lambda i: (i["date"], i["original"]))
    return out


def occurrences(event: dict, start: date, end: date) -> list[date]:
    """Dates on which the event occurs within [start, end], skips and moves applied."""
    return [i["date"] for i in instances(event, start, end)]


def _repeating(store: Store, event_id: int, on: str) -> dict:
    try:
        d = date.fromisoformat(on)
    except ValueError:
        raise ValueError(f"{on!r} is not a date") from None
    event = store.event(event_id)
    if not event or event.get("deleted_at"):
        raise ValueError("no such event")
    if not _rule(event.get("recurrence")):
        raise ValueError("only a repeating event has dates to skip or move; change a one-time event's date instead")
    if not rule_dates(event, d, d):
        raise ValueError(f"{event['title']} does not fall on {dates.month_day(d)}")
    return event


def skip_date(store: Store, event_id: int, on: str, note: str | None = None) -> dict:
    """Leave one date of a repeating event off the calendar. Returns the event. Raises ValueError."""
    event = _repeating(store, event_id, on)
    store.set_exception(event_id, on, "skip", note=note)
    return event


def move_date(store: Store, event_id: int, on: str, to: str, time_text: str | None = None,
              location: str | None = None, note: str | None = None) -> dict:
    """Hold one date of a repeating event on another day, or the same day at
    another time or place. Returns the event. Raises ValueError."""
    event = _repeating(store, event_id, on)
    try:
        date.fromisoformat(to or "")
    except ValueError:
        raise ValueError("a moved date needs the day it moves to") from None
    time_text, location = (time_text or "").strip(), (location or "").strip()
    if to == on and not time_text and not location:
        raise ValueError("nothing to move: give another day, time or place")
    store.set_exception(event_id, on, "move", new_date=to, time_text=time_text, location=location, note=note)
    return event


def restore_date(store: Store, event_id: int, on: str) -> bool:
    """Put a skipped or moved date back where the rule has it."""
    return store.clear_exception(event_id, on)


def coming_exceptions(event: dict, today: date | None = None) -> list[dict]:
    """The skipped and moved dates still ahead (a moved one counts until its new day passes), for the edit screen."""
    floor = (today or date.today()).isoformat()
    return [x for _, x in sorted(exceptions(event).items()) if max(x["occurrence_date"], x.get("new_date") or "") >= floor]


def line_text(event: dict, time_text: str | None = None) -> str:
    """``time_text`` is the time on a moved date, when it differs from the event's own."""
    text = event["title"]
    time_text = time_text or event.get("time_text")
    if time_text:
        text += f", {time_text}"
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
        for inst in instances(ev, start, window_end):
            text = (range_label(ev) or "") + line_text(ev, inst["time_text"])
            items = by_date.setdefault(inst["date"], [])
            if text.lower() not in (t.lower() for t in items):
                items.append(text)
    return [{"date": d.isoformat(), "items": items} for d, items in sorted(by_date.items())]


def upcoming(store: Store, days: int = 60, today: date | None = None) -> list[dict]:
    """Flat list for the public /calendar/ page."""
    today = today or date.today()
    end = today + timedelta(days=days)
    rows = []
    for ev in store.events():
        for inst in instances(ev, today, end):
            d = inst["date"]
            rows.append({
                "date": d.isoformat(),
                "label": dates.month_day(d),
                "month": d.strftime("%B %Y"),
                "weekday": d.strftime("%A"),
                "title": ev["title"],
                "time": inst["time_text"] or "",
                "location": inst["location"] or "",
                "recurring": bool(ev.get("recurrence")),
            })
    rows.sort(key=lambda r: (r["date"], r["time"] == "", r["time"]))
    return rows


def month_grid(store: Store, month: str | None = None, today: date | None = None) -> dict:
    """The on-screen calendar: one month (``YYYY-MM``, default this one) as
    whole weeks, Sunday first. Each day lists what happens on it and, struck
    through, the repeating dates that were skipped or moved away. A multi-day
    event shows on every one of its days."""
    today = today or date.today()
    try:
        year, mon = (int(x) for x in (month or "").split("-"))
        first = date(year, mon, 1)
        if not 2000 <= year <= 2100:
            raise ValueError
    except ValueError:
        first = today.replace(day=1)
    weeks = _cal.Calendar(firstweekday=6).monthdatescalendar(first.year, first.month)
    start, end = weeks[0][0], weeks[-1][-1]
    by_date: dict[date, list[dict]] = {}
    for ev in store.events():
        base = {"event_id": ev["id"], "title": ev["title"], "featured": bool(ev.get("featured")),
                "recurring": bool(_rule(ev.get("recurrence")))}
        if not base["recurring"]:
            a = date.fromisoformat(ev["start_date"])
            b = max(a, date.fromisoformat(ev["end_date"])) if ev.get("end_date") else a
            d = max(a, start)
            while d <= min(b, end):
                by_date.setdefault(d, []).append({**base, "status": "", "time": ev.get("time_text") or "", "location": ev.get("location") or "",
                                                  "past": b < today, "original": ev["start_date"], "other_label": "", "note": ""})
                d += timedelta(days=1)
            continue
        for inst in instances(ev, start, end, with_gaps=True):
            x = inst["exception"] or {}
            other = ""
            if inst["status"] == "moved" and x["new_date"] != x["occurrence_date"]:
                other = x["occurrence_date"]
            elif inst["status"] == "moved_away":
                other = x["new_date"]
            by_date.setdefault(inst["date"], []).append({
                **base, "status": inst["status"], "time": inst["time_text"] or "", "location": inst["location"] or "",
                "past": inst["date"] < today, "original": inst["original"].isoformat(),
                "other_label": dates.short_month_day(other) if other else "", "note": x.get("note") or ""})
    for items in by_date.values():
        items.sort(key=lambda i: i["status"] in ("skipped", "moved_away"))  # what happens first, the gaps after
    prev, nxt = first - timedelta(days=1), (first + timedelta(days=32)).replace(day=1)
    return {
        "month": first.strftime("%Y-%m"), "label": first.strftime("%B %Y"),
        "prev": prev.strftime("%Y-%m"), "prev_label": prev.strftime("%B"),
        "next": nxt.strftime("%Y-%m"), "next_label": nxt.strftime("%B"),
        "this_month": today.strftime("%Y-%m"),
        "weeks": [[{"date": d.isoformat(), "day": d.day, "weekday": d.strftime("%a"), "in_month": d.month == first.month,
                    "today": d == today, "entries": by_date.get(d, [])} for d in week] for week in weeks],
    }


PUBLIC_MONTHS = 3  # the month view on the public /calendar/ page: this month and the next two


def _time_key(text: str) -> tuple:
    """Order a day's entries by the clock: '8 a.m.' before 'noon' before '6pm'; no time goes last."""
    t = (text or "").lower()
    if not t.strip():
        return (2, 0, "")
    if t.strip().startswith("noon"):
        return (0, 12 * 60, t)
    m = re.match(r"\s*(\d{1,2})(?::(\d{2}))?[^ap]*([ap])?", t)
    if not m:
        return (1, 0, t)
    hour = int(m.group(1)) % 12 + (12 if m.group(3) == "p" else 0)
    return (0, hour * 60 + int(m.group(2) or 0), t)


def public_months(store: Store, count: int = PUBLIC_MONTHS, today: date | None = None) -> list[dict]:
    """The month view of the public /calendar/ page: ``month_grid`` for this
    month and the ones after it, with only what a visitor should see. The
    skipped and moved-away dates, the office's notes on them and the event
    ids stay in the app; a moved date shows on its new day. "Today" is not
    marked here, since the page is built once and read for days."""
    today = today or date.today()
    first = today.replace(day=1)
    out = []
    for _ in range(count):
        grid = month_grid(store, first.strftime("%Y-%m"), today)
        weeks = [[{"date": day["date"], "day": day["day"], "weekday": day["weekday"], "in_month": day["in_month"],
                   "entries": sorted(({"title": e["title"], "time": e["time"], "location": e["location"], "recurring": e["recurring"]}
                                      for e in day["entries"] if e["status"] not in ("skipped", "moved_away")),
                                     key=lambda e: _time_key(e["time"]))}
                  for day in week] for week in grid["weeks"]]
        out.append({"month": grid["month"], "label": grid["label"], "prev_label": grid["prev_label"], "next_label": grid["next_label"], "weeks": weeks})
        first = (first + timedelta(days=32)).replace(day=1)
    return out


PRINT_DIR = "src/calendar-print"   # Eleventy copies it to /calendar/print/


def write_month_pdfs(months: list[dict], repo: Path, as_of: date | None = None) -> list[Path]:
    """One landscape, one-page PDF per month of the public month view, which is
    what "Print this month" on /calendar/ opens: a browser's own print cannot be
    made to choose landscape or to fit a page, a PDF is both already. Each
    month gains ``pdf``, its address on the site. The file name carries a hash
    of what is on the page, so an unchanged month is left alone (no churn in
    git, and a changed month gets a new address no cache can hold an old copy
    of); files for months no longer shown are removed. A month too crowded
    to fit a page at a readable size gets no file and no ``pdf``, and the
    page prints that one from the browser."""
    from builder.pipeline import pdf as pdfmod, render

    out = repo / PRINT_DIR
    template = (config.TEMPLATES_DIR / "calendar-month.html.j2").read_text()
    names = {}
    for m in months:
        digest = hashlib.sha1((template + json.dumps(m, sort_keys=True, ensure_ascii=False)).encode()).hexdigest()[:8]
        names[m["month"]] = f"fpc-calendar-{m['month']}-{digest}.pdf"
    missing = [m for m in months if not (out / names[m["month"]]).exists()]
    if missing:
        out.mkdir(parents=True, exist_ok=True)
        tpl = render._env().get_template("calendar-month.html.j2")
        with tempfile.TemporaryDirectory() as tmp, pdfmod.Renderer() as renderer:
            for m in missing:
                html = Path(tmp) / f"{m['month']}.html"
                html.write_text(tpl.render(m=m, as_of=dates.long_date(as_of or date.today())))
                draft = Path(tmp) / f"{m['month']}.pdf"
                try:
                    renderer.fitted_pdf(html, draft)
                    if pdfmod.pdf_info(draft)["pages"] != 1:
                        raise ValueError("came out as more than one page")
                except ValueError as e:
                    logging.getLogger("builder.calendar").warning("no printable PDF for %s: %s", m["label"], e)
                    del names[m["month"]]
                    continue
                draft.replace(out / names[m["month"]])
    for old in out.glob("fpc-calendar-*.pdf"):
        if old.name not in names.values():
            old.unlink()
    for m in months:
        if m["month"] in names:
            m["pdf"] = f"/calendar/print/{names[m['month']]}"
    return [out / n for n in names.values()]


def _vevent(ev: dict, stamp: str, start: date, end: date, time_text: str | None, location: str | None) -> Event:
    """``stamp`` is when this was last revised in the table: DTSTAMP is that, not the moment the
    file was written, so the feed is byte-for-byte the same until an event changes."""
    e = Event()
    e.add("uid", ev["uid"])
    e.add("dtstamp", datetime.fromisoformat(stamp).astimezone(timezone.utc))
    e.add("summary", line_text(ev, time_text))
    e.add("dtstart", start)
    e.add("dtend", end + timedelta(days=1))  # all-day events: DTEND is exclusive
    if location:
        e.add("location", location)
    desc = " ".join(x for x in [time_text, ev.get("notes")] if x)
    if desc:
        e.add("description", desc)
    return e


def build_ics(store: Store) -> bytes:
    cal = Calendar()
    cal.add("prodid", "-//First Presbyterian Church of Corinth//Bulletin Builder//EN")
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")
    cal.add("method", "PUBLISH")
    cal.add("x-wr-calname", "FPC Corinth Church Calendar")
    cal.add("x-wr-timezone", "America/Chicago")
    cal.add("refresh-interval;value=duration", "P1D")
    for ev in store.events():
        start = date.fromisoformat(ev["start_date"])
        end = date.fromisoformat(ev["end_date"]) if ev.get("end_date") else start
        e = _vevent(ev, ev["updated_at"], start, end, ev.get("time_text"), ev.get("location"))
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
        exc = sorted(exceptions(ev).items())
        skipped = [d for d, x in exc if x["kind"] == "skip"]
        if skipped:
            e.add("exdate", skipped)
        e.add("last-modified", datetime.fromisoformat(ev["updated_at"]))
        cal.add_component(e)
        for d, x in exc:  # a moved date: the same UID, RECURRENCE-ID naming the date it replaces
            if x["kind"] != "move":
                continue
            new = date.fromisoformat(x["new_date"])
            o = _vevent(ev, x["updated_at"], new, new, x.get("time_text") or ev.get("time_text"), x.get("location") or ev.get("location"))
            o.add("recurrence-id", d)
            o.add("last-modified", datetime.fromisoformat(x["updated_at"]))
            cal.add_component(o)
    return cal.to_ical()


LOCAL_TZ = ZoneInfo("America/Chicago")


def local_today() -> date:
    """Today in Corinth. The server keeps UTC, where the date turns over at six in the evening."""
    return datetime.now(LOCAL_TZ).date()


def write_site_files(store: Store, repo: Path | None = None, today: date | None = None) -> list[Path]:
    """Write src/calendar.ics, src/_data/calendarEvents.json and the printable month PDFs for Eleventy.

    Everything here is a function of the events table and the current month,
    and a file is only rewritten when its content differs, so running this
    again changes nothing until an event changes or a new month begins (that
    is what lets ``publish.publish_calendar`` run after every edit and every
    night without a commit each time). The list starts at the first of the
    month, like the grid; the page itself hides the days that have passed."""
    repo = repo or config.REPO
    today = today or local_today()
    ics_path = repo / "src/calendar.ics"
    data_path = repo / "src/_data/calendarEvents.json"
    ics = build_ics(store)
    if not ics_path.exists() or ics_path.read_bytes() != ics:
        ics_path.write_bytes(ics)
    months = public_months(store, today=today)
    try:
        pdfs = write_month_pdfs(months, repo, as_of=today)
    except Exception as e:  # the calendar still publishes; a month without ``pdf`` prints from the browser instead
        logging.getLogger("builder.calendar").warning("calendar PDFs not written: %s", e)
        pdfs = []
    first = today.replace(day=1)
    last = next(date.fromisoformat(d["date"]) for d in reversed(months[-1]["weeks"][-1]) if d["in_month"])  # the last day of the last month shown
    payload = {
        "generated": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "subscribe_url": f"{config.SITE_URL}/calendar.ics",
        "events": upcoming(store, (last - first).days, today=first),
        "months": months,
    }
    try:
        current = json.loads(data_path.read_text())
    except (OSError, ValueError):
        current = None
    if current is None or {**current, "generated": None} != {**payload, "generated": None}:
        data_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return [ics_path, data_path, *pdfs]


def validate_ics(data: bytes) -> int:
    """Round-trip through icalendar; returns the number of VEVENTs."""
    cal = Calendar.from_ical(data)
    return sum(1 for c in cal.walk() if c.name == "VEVENT")
