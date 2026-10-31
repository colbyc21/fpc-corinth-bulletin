import json

import pytest

from builder import config
from builder.pipeline import calendar_feed, week as weekmod


def test_nov1_week_json_validates():
    week = weekmod.load_week("2026-11-01")
    assert week["date"] == "2026-11-01"
    assert weekmod.subtitle(week) == "Twenty-third Sunday after Pentecost"


def test_default_pages_cover_every_item():
    week = weekmod.load_week("2026-11-01")
    pages = weekmod.default_pages(week["liturgy"]["items"])
    placed = [i for p in pages.values() for i in p]
    assert sorted(placed) == sorted(it["id"] for it in week["liturgy"]["items"])
    assert pages["p3"][-1] == "hymn1"
    assert pages["p4"] == ["invocation", "confession-call"]
    assert pages["p6"][0] == "reading"


def test_validation_rejects_unknown_note_target():
    week = weekmod.load_week("2026-11-01")
    week["liturgy"]["notes"].append({"for": "nope", "label": "x", "text": "y"})
    with pytest.raises(ValueError):
        weekmod.validate(week)


def test_scaffold_is_valid():
    week = weekmod.scaffold("2026-11-08")
    weekmod.validate(week)
    assert week["liturgical_day"] == "Twenty-fourth Sunday after Pentecost"


def test_week_calendar_matches_printed_nov1(store):
    lines = calendar_feed.week_calendar(store, "2026-11-01")
    printed = weekmod.load_week("2026-11-01")["calendar"]
    assert [(l["date"], l["items"]) for l in lines] == [(c["date"], c["items"]) for c in printed]


def test_ics_round_trips(store):
    data = calendar_feed.build_ics(store)
    n = calendar_feed.validate_ics(data)
    assert n == len(store.events())
    assert b"RRULE:FREQ=WEEKLY;BYDAY=TU" in data
    assert b"X-WR-CALNAME:FPC Corinth Church Calendar" in data


def test_monthly_recurrence():
    from datetime import date
    ev = {"title": "Session Meeting", "start_date": "2026-11-01", "recurrence": "MONTHLY;BYDAY=1SU"}
    got = calendar_feed.occurrences(ev, date(2026, 11, 1), date(2027, 1, 31))
    assert [d.isoformat() for d in got] == ["2026-11-01", "2026-12-06", "2027-01-03"]
    last = {"title": "Men's breakfast", "start_date": "2026-11-01", "recurrence": "MONTHLY;BYDAY=-1TH", "until_date": "2026-12-31"}
    got = calendar_feed.occurrences(last, date(2026, 11, 1), date(2027, 2, 28))
    assert [d.isoformat() for d in got] == ["2026-11-26", "2026-12-31"]
    assert calendar_feed.describe_recurrence("MONTHLY;BYDAY=-1TH") == "the last Thursday of the month"
    assert calendar_feed.describe_recurrence("WEEKLY;BYDAY=TU") == "every Tuesday"
    assert calendar_feed.make_recurrence("MONTHLY", "2", "WE") == "MONTHLY;BYDAY=2WE"
    assert calendar_feed.recurrence_parts("MONTHLY;BYDAY=2WE") == {"freq": "MONTHLY", "nth": "2", "day": "WE"}
    assert calendar_feed.make_recurrence("", "", "") is None
    import pytest
    with pytest.raises(ValueError):
        calendar_feed.make_recurrence("MONTHLY", "", "WE")


def test_monthly_event_in_ics(store):
    store.upsert_event({"title": "Session Meeting", "start_date": "2026-11-01", "recurrence": "MONTHLY;BYDAY=1SU"})
    data = calendar_feed.build_ics(store)
    assert b"RRULE:FREQ=MONTHLY;BYDAY=1SU" in data
    calendar_feed.validate_ics(data)


def test_upcoming_is_sorted(store):
    from datetime import date
    rows = calendar_feed.upcoming(store, 60, today=date(2026, 11, 1))
    assert rows == sorted(rows, key=lambda r: (r["date"], r["time"] == "", r["time"]))
    assert any(r["title"] == "37th Community Thanksgiving Meal" for r in rows)


def test_roster_seed_has_nov1(store):
    serve = store.serve_for("2026-11-01")
    assert serve["assisting_elder"] == "Dr. John Shipp"
    assert serve["circle_of_prayer"] == "Jason & Jennifer LeGoff"
