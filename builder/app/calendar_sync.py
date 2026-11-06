"""The public calendar follows the Calendar screen by itself.

An edit there calls ``request``; a few minutes after the *last* edit
(``config.CALENDAR_PUSH_DELAY``, so a run of edits is one push and one
deploy) the calendar files are regenerated and pushed by
``publish.publish_calendar``. A restart inside those minutes loses the
pending push; the nightly timer (``deploy/bulletin-calendar.timer``) picks it
up, as it does the turn of a month. What happened is in the audit log as
``calendar.publish`` / ``calendar.publish.failed``, and ``status`` reads it
back for the Calendar screen.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone

from builder import config
from builder.pipeline import calendar_feed, publish
from builder.pipeline.store import Store

log = logging.getLogger("builder.calendar")
_lock = threading.Lock()
_timer: threading.Timer | None = None
_due: datetime | None = None


def request(store: Store, user: str | None = None) -> None:
    """The events table changed: push the public calendar once the edits stop."""
    global _timer, _due
    if not config.CALENDAR_AUTOPUBLISH:
        return
    with _lock:
        if _timer:
            _timer.cancel()
        _timer = threading.Timer(config.CALENDAR_PUSH_DELAY, _run, args=(store, user))
        _timer.daemon = True
        _due = datetime.now(timezone.utc) + timedelta(seconds=config.CALENDAR_PUSH_DELAY)
        _timer.start()


def _run(store: Store, user: str | None) -> None:
    global _timer, _due
    with _lock:
        _timer, _due = None, None
    try:
        send(store, user)
    except Exception:
        log.exception("the public calendar was not updated")


def send(store: Store, user: str | None = None) -> dict:
    """Regenerate and push now. A failure is recorded for the Calendar screen and raised."""
    global _timer, _due
    with _lock:
        if _timer:
            _timer.cancel()
        _timer, _due = None, None
    try:
        return publish.publish_calendar(store=store, user=user)
    except Exception as e:
        store.log("calendar.publish.failed", user, str(e)[:500])
        raise


def _local(ts: str | datetime) -> str:
    """'November 6 at 4:12 p.m.' in Corinth."""
    d = (datetime.fromisoformat(ts) if isinstance(ts, str) else ts).astimezone(calendar_feed.LOCAL_TZ)
    return f"{d.strftime('%B')} {d.day} at {d.strftime('%-I:%M')} {'a.m.' if d.hour < 12 else 'p.m.'}"


def status(store: Store) -> dict:
    """For the Calendar screen: is a push waiting, when did the calendar last go out, did the last try fail."""
    sent = store.last_audit("calendar.publish", "publish")   # the weekly publish carries the calendar too
    last = store.last_audit("calendar.publish", "calendar.publish.failed")
    failed = last if last and last["action"] == "calendar.publish.failed" else None
    return {
        "auto": config.CALENDAR_AUTOPUBLISH,
        "delay_minutes": max(1, round(config.CALENDAR_PUSH_DELAY / 60)),
        "due": _local(_due) if _due else None,
        "sent": _local(sent["ts"]) if sent else None,
        "failed": {"when": _local(failed["ts"]), "why": failed["detail"]} if failed else None,
    }
