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


def test_upcoming_is_sorted(store):
    from datetime import date
    rows = calendar_feed.upcoming(store, 60, today=date(2026, 11, 1))
    assert rows == sorted(rows, key=lambda r: (r["date"], r["time"] == "", r["time"]))
    assert any(r["title"] == "37th Community Thanksgiving Meal" for r in rows)


def test_roster_seed_has_nov1(store):
    serve = store.serve_for("2026-11-01")
    assert serve["assisting_elder"] == "Dr. John Shipp"
    assert serve["circle_of_prayer"] == "Jason & Jennifer LeGoff"
