"""Page-view stats for the public site, kept on basecamp.

Every page of bulletin.fpccorinth.org carries a few lines of script (added at
build time by ``.eleventy.js`` from ``src/_includes/stats-beacon.js``) that
post the page's path and referrer to ``/hit`` on this app. ``record`` turns
that into one row of the ``hits`` table; ``summary`` is what the Stats screen
shows.

Nothing identifies a person: no cookie is set, the address is never stored,
and a visitor is a hash of address + browser under a random salt that is
thrown away when the day ends, so "visitors" means "counted once a day" and
yesterday's hash cannot be matched to today's.
"""
from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from builder import config
from builder.pipeline import dates
from builder.pipeline.store import Store

TZ = ZoneInfo("America/Chicago")
SERVICE_HOURS = (10, 11)          # Sunday 10:00-11:59: worship is at 10:45
MAX_PER_VISITOR_PER_DAY = 300     # a stuck reload loop or a script, not a reader
MAX_PER_DAY = 20_000              # keeps a flood from growing the table
CHART_DAYS = 56

PATH_RE = re.compile(r"^/[A-Za-z0-9/_.\-]{0,120}$")
BOT_RE = re.compile(r"bot|crawl|spider|slurp|preview|headless|lighthouse|pingdom|uptime|monitor|curl|wget|python|httpx|scrapy|facebookexternalhit", re.I)
PAGE_LABELS = {"/": "Front page", "/archive": "Past Bulletins", "/calendar": "Church Calendar", "/hymns": "Hymn Index",
               "/scripture": "Scripture Index", "/about": "About & Service Times"}
DEVICE_LABELS = {"phone": "Phone", "tablet": "Tablet", "desktop": "Computer"}


def normalize_path(raw: object) -> str | None:
    """'/scroll/2026-11-08.html' and '/scroll/2026-11-08/' are one page: '/scroll/2026-11-08'."""
    if not isinstance(raw, str) or not PATH_RE.match(raw) or ".." in raw or "//" in raw:
        return None
    path = raw
    if path.endswith("/index.html"):
        path = path[: -len("index.html")]
    elif path.endswith(".html"):
        path = path[: -len(".html")]
    return path.rstrip("/") or "/"


def device_of(ua: str) -> str:
    if re.search(r"ipad|tablet|kindle|silk", ua, re.I) or (re.search(r"android", ua, re.I) and not re.search(r"mobile", ua, re.I)):
        return "tablet"
    if re.search(r"iphone|ipod|android|mobile", ua, re.I):
        return "phone"
    return "desktop"


def referrer_host(ref: object) -> str | None:
    """The outside site a visitor came from; none for a move within the site or a typed/opened link."""
    if not isinstance(ref, str) or not ref.startswith(("http://", "https://")):
        return None
    host = (urlsplit(ref[:500]).hostname or "").lower().removeprefix("www.")
    if not host or host == urlsplit(config.SITE_URL).hostname or not re.match(r"^[a-z0-9.\-]{1,80}$", host):
        return None
    return host


def record(store: Store, *, path: object, referrer: object = None, ua: str = "", ip: str = "", now: datetime | None = None) -> bool:
    """Count one page view. False when it is not one to count (a bot, a bad path, a flood)."""
    path = normalize_path(path)
    if path is None or not ua or BOT_RE.search(ua):
        return False
    now = now or datetime.now(timezone.utc)
    local = now.astimezone(TZ)
    day = local.date().isoformat()
    visitor = hashlib.sha256(f"{store.visitor_salt(day)}|{ip}|{ua}".encode()).hexdigest()[:16]
    total, mine = store.hit_counts(day, visitor)
    if total >= MAX_PER_DAY or mine >= MAX_PER_VISITOR_PER_DAY:
        return False
    store.add_hit(now.replace(microsecond=0).isoformat(), day, local.hour, path, visitor, referrer_host(referrer), device_of(ua))
    return True


def page_label(path: str) -> str:
    m = re.match(r"^/scroll/(\d{4}-\d{2}-\d{2})$", path)
    if m:
        try:
            return f"Bulletin, {dates.month_day(m.group(1))}"
        except ValueError:
            return path
    return PAGE_LABELS.get(path, path)


def _nice_max(n: int) -> int:
    """The top of the chart's axis: the next round number at or above ``n`` that splits into four whole ticks."""
    step = 1
    while True:
        for mult in (1, 2, 5):
            if n <= 4 * mult * step:
                return 4 * mult * step
        step *= 10


def _delta(now: int, before: int) -> dict | None:
    if not before:
        return None
    pct = round((now - before) * 100 / before)
    return {"pct": pct, "text": f"{'+' if pct > 0 else '−' if pct < 0 else ''}{abs(pct)}%"}


def _bars(rows: list[dict], label=lambda s: s) -> list[dict]:
    top = max((r["views"] for r in rows), default=0)
    return [{**r, "label": label(r["label"]), "pct": round(r["views"] * 100 / top, 1) if top else 0} for r in rows]


def summary(store: Store, today: date | None = None) -> dict:
    """Everything the Stats screen shows, for the days up to ``today`` (Corinth's date)."""
    today = today or datetime.now(TZ).date()
    iso = lambda d: d.isoformat()  # noqa: E731
    week = store.hits_total(iso(today - timedelta(days=6)), iso(today))
    before = store.hits_total(iso(today - timedelta(days=13)), iso(today - timedelta(days=7)))

    start = today - timedelta(days=CHART_DAYS - 1)
    by_day = store.hits_daily(iso(start), iso(today))
    days = []
    for i in range(CHART_DAYS):
        d = start + timedelta(days=i)
        row = by_day.get(iso(d), {})
        days.append({"date": iso(d), "label": d.strftime("%a %b %-d"), "tick": d.strftime("%b %-d") if d.weekday() == 6 else "",
                     "views": row.get("views", 0), "visitors": row.get("visitors", 0)})
    top = _nice_max(max(d["views"] for d in days))

    bulletins = store.hits_by_bulletin(12, SERVICE_HOURS)
    last_sunday = today - timedelta(days=(today.weekday() + 1) % 7)
    sunday = next((b for b in bulletins if b["date"] == iso(last_sunday)), None)
    month = (iso(today - timedelta(days=29)), iso(today))
    first = store.first_hit_day()
    return {
        "today": iso(today), "since": first, "empty": first is None,
        "week": week, "week_delta": {"views": _delta(week["views"], before["views"]), "visitors": _delta(week["visitors"], before["visitors"])},
        "last_sunday": iso(last_sunday), "sunday": sunday,
        "days": days, "axis_max": top, "ticks": [top * i // 4 for i in range(5)],
        "bulletins": bulletins,
        "pages": _bars(store.hits_grouped("path", *month, limit=12), page_label),
        "referrers": _bars(store.hits_grouped("referrer", *month, limit=10)),
        "devices": _bars(store.hits_grouped("device", *month, limit=3), lambda d: DEVICE_LABELS.get(d, d)),
    }
