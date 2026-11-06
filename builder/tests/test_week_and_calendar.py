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


def test_roster_gains_the_flowers_column_on_an_older_database(tmp_path):
    import sqlite3
    from builder.pipeline.store import Store
    path = tmp_path / "old.sqlite"
    db = sqlite3.connect(path)
    db.execute("create table rosters (sunday text primary key, assisting_elder text, greeters text, ushers text, acolytes text,"
               " nursery text, circle_of_prayer text, childrens_church text, av_engineer text, updated_at text not null)")
    db.execute("insert into rosters (sunday, greeters, updated_at) values ('2026-11-08', 'Hunter & Anna McCalla', 'x')")
    db.commit(); db.close()
    s = Store(path)
    assert s.serve_for("2026-11-08")["greeters"] == "Hunter & Anna McCalla" and s.flowers_for("2026-11-08") is None
    s.upsert_roster("2026-11-08", {**s.serve_for("2026-11-08"), "flowers": "Jan Pike"})
    assert s.flowers_for("2026-11-08") == "Jan Pike" and s.serve_for("2026-11-08")["greeters"] == "Hunter & Anna McCalla"
    assert "flowers" not in s.serve_for("2026-11-08")
    s.close(); Store(path).close()  # opening again does not try to add the column twice


# ── one date of a repeating event skipped or moved ──────────────────
@pytest.fixture()
def cal_store(tmp_path):
    from builder.pipeline.store import Store
    s = Store(tmp_path / "cal.sqlite")
    s.upsert_event({"title": "Messy Bible Study", "start_date": "2026-10-20", "time_text": "8 a.m.",
                    "location": "Youth House", "recurrence": "WEEKLY;BYDAY=TU"})
    s.upsert_event({"title": "Session Meeting", "start_date": "2026-11-01", "recurrence": "MONTHLY;BYDAY=1SU"})
    s.upsert_event({"title": "37th Community Thanksgiving Meal", "start_date": "2026-11-26"})
    yield s
    s.close()


def _id(store, title):
    return next(e["id"] for e in store.events() if e["title"] == title)


def test_a_skipped_date_drops_out_everywhere(cal_store):
    from datetime import date
    study = _id(cal_store, "Messy Bible Study")
    calendar_feed.skip_date(cal_store, study, "2026-11-24", "Thanksgiving week")
    ev = cal_store.event(study)
    got = calendar_feed.occurrences(ev, date(2026, 11, 15), date(2026, 12, 5))
    assert [d.isoformat() for d in got] == ["2026-11-17", "2026-12-01"]
    assert [l["date"] for l in calendar_feed.week_calendar(cal_store, "2026-11-22")] == ["2026-11-26"]
    assert "2026-11-24" not in [r["date"] for r in calendar_feed.upcoming(cal_store, 60, today=date(2026, 11, 6))]
    data = calendar_feed.build_ics(cal_store)
    assert b"EXDATE;VALUE=DATE:20261124" in data and b"RRULE:FREQ=WEEKLY;BYDAY=TU" in data
    assert calendar_feed.validate_ics(data) == 3
    # putting it back restores the rule's date
    assert calendar_feed.restore_date(cal_store, study, "2026-11-24") is True
    assert calendar_feed.week_calendar(cal_store, "2026-11-22")[0] == {"date": "2026-11-24", "items": ["Messy Bible Study, 8 a.m."]}
    assert b"EXDATE" not in calendar_feed.build_ics(cal_store)


def test_a_moved_date_lands_on_its_new_day_with_its_new_time(cal_store):
    from datetime import date
    session = _id(cal_store, "Session Meeting")
    calendar_feed.move_date(cal_store, session, "2026-12-06", "2026-12-13", "12:15 p.m.", "Parlor")
    ev = cal_store.event(session)
    got = calendar_feed.instances(ev, date(2026, 11, 1), date(2027, 1, 31))
    assert [(i["date"].isoformat(), i["status"], i["time_text"], i["location"]) for i in got] == [
        ("2026-11-01", "", None, None), ("2026-12-13", "moved", "12:15 p.m.", "Parlor"), ("2027-01-03", "", None, None)]
    # the bulletin for Dec 6 no longer lists it; the one for Dec 13 does, with the time
    assert all("Session Meeting" not in " ".join(l["items"]) for l in calendar_feed.week_calendar(cal_store, "2026-12-06", horizon_days=6))
    assert {"date": "2026-12-13", "items": ["Session Meeting, 12:15 p.m."]} in calendar_feed.week_calendar(cal_store, "2026-12-13")
    row = next(r for r in calendar_feed.upcoming(cal_store, 60, today=date(2026, 11, 6)) if r["date"] == "2026-12-13")
    assert (row["title"], row["time"], row["location"], row["weekday"]) == ("Session Meeting", "12:15 p.m.", "Parlor", "Sunday")
    # a window that holds only the new day still finds it; one that holds only the old day finds nothing
    assert calendar_feed.occurrences(ev, date(2026, 12, 10), date(2026, 12, 20)) == [date(2026, 12, 13)]
    assert calendar_feed.occurrences(ev, date(2026, 12, 1), date(2026, 12, 8)) == []
    # the feed: one repeating event, plus an override that names the date it replaces
    data = calendar_feed.build_ics(cal_store)
    assert calendar_feed.validate_ics(data) == 4
    assert b"RECURRENCE-ID;VALUE=DATE:20261206" in data and b"DTSTART;VALUE=DATE:20261213" in data
    assert b"SUMMARY:Session Meeting\\, 12:15 p.m." in data and b"EXDATE" not in data
    from icalendar import Calendar
    uids = [str(c["uid"]) for c in Calendar.from_ical(data).walk() if c.name == "VEVENT" and "Session" in str(c["summary"])]
    assert len(uids) == 2 and len(set(uids)) == 1


def test_a_date_can_keep_its_day_and_change_only_its_time(cal_store):
    from datetime import date
    study = _id(cal_store, "Messy Bible Study")
    calendar_feed.move_date(cal_store, study, "2026-11-10", "2026-11-10", time_text="9 a.m.")
    assert calendar_feed.week_calendar(cal_store, "2026-11-08")[0] == {"date": "2026-11-10", "items": ["Messy Bible Study, 9 a.m."]}
    gaps = calendar_feed.instances(cal_store.event(study), date(2026, 11, 10), date(2026, 11, 10), with_gaps=True)
    assert [i["status"] for i in gaps] == ["moved"]  # no struck-through twin on the same day
    # a second change to the same date replaces the first
    calendar_feed.skip_date(cal_store, study, "2026-11-10")
    assert [x["kind"] for x in cal_store.event(study)["exceptions"]] == ["skip"]
    assert cal_store.event(study)["exceptions"][0]["time_text"] is None


def test_skip_and_move_refuse_what_makes_no_sense(cal_store):
    study, meal = _id(cal_store, "Messy Bible Study"), _id(cal_store, "37th Community Thanksgiving Meal")
    with pytest.raises(ValueError, match="does not fall on November 25"):
        calendar_feed.skip_date(cal_store, study, "2026-11-25")       # a Wednesday
    with pytest.raises(ValueError, match="does not fall"):
        calendar_feed.skip_date(cal_store, study, "2026-10-13")       # before it started
    with pytest.raises(ValueError, match="only a repeating event"):
        calendar_feed.skip_date(cal_store, meal, "2026-11-26")
    with pytest.raises(ValueError, match="needs the day it moves to"):
        calendar_feed.move_date(cal_store, study, "2026-11-24", "")
    with pytest.raises(ValueError, match="nothing to move"):
        calendar_feed.move_date(cal_store, study, "2026-11-24", "2026-11-24")
    with pytest.raises(ValueError, match="no such event"):
        calendar_feed.skip_date(cal_store, 9999, "2026-11-24")
    with pytest.raises(ValueError, match="not a date"):
        calendar_feed.skip_date(cal_store, study, "next Tuesday")
    assert cal_store.event(study)["exceptions"] == []
    assert calendar_feed.restore_date(cal_store, study, "2026-11-24") is False


def test_an_exception_stops_applying_when_the_rule_moves_off_its_date(cal_store):
    from datetime import date
    study = _id(cal_store, "Messy Bible Study")
    calendar_feed.skip_date(cal_store, study, "2026-11-24")
    calendar_feed.move_date(cal_store, study, "2026-12-01", "2026-12-03")
    ev = cal_store.event(study)
    cal_store.upsert_event({**ev, "recurrence": "WEEKLY;BYDAY=WE"}, study)
    ev = cal_store.event(study)
    got = calendar_feed.occurrences(ev, date(2026, 11, 22), date(2026, 12, 5))
    assert [d.isoformat() for d in got] == ["2026-11-25", "2026-12-02"]   # every Wednesday, and no stray Thursday
    data = calendar_feed.build_ics(cal_store)
    assert b"EXDATE" not in data and b"RECURRENCE-ID" not in data
    assert calendar_feed.coming_exceptions(ev, date(2026, 11, 6)) == []


def test_coming_exceptions_lists_what_is_still_ahead(cal_store):
    from datetime import date
    study = _id(cal_store, "Messy Bible Study")
    calendar_feed.skip_date(cal_store, study, "2026-10-27")
    calendar_feed.move_date(cal_store, study, "2026-11-03", "2026-11-07")
    calendar_feed.skip_date(cal_store, study, "2026-11-24")
    ahead = calendar_feed.coming_exceptions(cal_store.event(study), date(2026, 11, 6))
    assert [x["occurrence_date"] for x in ahead] == ["2026-11-03", "2026-11-24"]   # Nov 3 still counts: its new day is tomorrow


def test_month_grid_is_whole_weeks_from_sunday_with_the_gaps_marked(cal_store):
    from datetime import date
    study = _id(cal_store, "Messy Bible Study")
    calendar_feed.skip_date(cal_store, study, "2026-11-24", "Thanksgiving week")
    calendar_feed.move_date(cal_store, study, "2026-11-10", "2026-11-11")
    cal_store.upsert_event({"title": "Edge", "start_date": "2026-11-27", "end_date": "2026-11-29"})
    g = calendar_feed.month_grid(cal_store, "2026-11", today=date(2026, 11, 6))
    assert (g["label"], g["prev"], g["next"], g["this_month"]) == ("November 2026", "2026-10", "2026-12", "2026-11")
    assert all(len(w) == 7 for w in g["weeks"])
    assert g["weeks"][0][0]["date"] == "2026-11-01" and g["weeks"][-1][-1]["date"] == "2026-12-05"
    days = {d["date"]: d for w in g["weeks"] for d in w}
    assert days["2026-11-06"]["today"] and not days["2026-12-01"]["in_month"]

    def on(d):
        return [(e["title"], e["status"], e["other_label"]) for e in days[d]["entries"]]
    assert on("2026-11-01") == [("Session Meeting", "", "")]
    assert on("2026-11-10") == [("Messy Bible Study", "moved_away", "Nov 11")]
    assert on("2026-11-11") == [("Messy Bible Study", "moved", "Nov 10")]
    assert on("2026-11-24") == [("Messy Bible Study", "skipped", "")] and days["2026-11-24"]["entries"][0]["note"] == "Thanksgiving week"
    assert on("2026-11-26") == [("37th Community Thanksgiving Meal", "", "")]
    assert [on(d) for d in ("2026-11-27", "2026-11-28", "2026-11-29")] == [[("Edge", "", "")]] * 3   # every day of a multi-day event
    assert on("2026-12-01") == [("Messy Bible Study", "", "")]   # the trailing days of the last week are filled in too
    # every repeating entry carries the date the rule gave it, which is what a click acts on
    assert days["2026-11-11"]["entries"][0]["original"] == "2026-11-10"
    # a month that is not one falls back to the current month
    assert calendar_feed.month_grid(cal_store, "garbage", today=date(2026, 11, 6))["month"] == "2026-11"
    assert calendar_feed.month_grid(cal_store, "2026-13", today=date(2026, 11, 6))["month"] == "2026-11"
    assert calendar_feed.month_grid(cal_store, "2026-12", today=date(2026, 11, 6))["next"] == "2027-01"


def test_public_months_show_what_happens_and_nothing_from_the_office(cal_store):
    from datetime import date
    study = _id(cal_store, "Messy Bible Study")
    calendar_feed.skip_date(cal_store, study, "2026-11-24", "Thanksgiving week")
    calendar_feed.move_date(cal_store, study, "2026-11-10", "2026-11-11", "6:30 p.m.", "Parlor", "the Youth House is being painted")
    cal_store.upsert_event({"title": "Choir Supper", "start_date": "2026-11-11", "time_text": "5pm"})
    cal_store.upsert_event({"title": "Coat Drive", "start_date": "2026-11-11"})
    cal_store.upsert_event({"title": "Lunch Bunch", "start_date": "2026-11-11", "time_text": "noon"})
    months = calendar_feed.public_months(cal_store, today=date(2026, 11, 20))
    assert [(m["month"], m["label"]) for m in months] == [("2026-11", "November 2026"), ("2026-12", "December 2026"), ("2027-01", "January 2027")]
    assert (months[0]["prev_label"], months[0]["next_label"]) == ("October", "December")
    nov = {d["date"]: d for w in months[0]["weeks"] for d in w}
    assert nov["2026-11-01"]["weekday"] == "Sun" and len(months[0]["weeks"][0]) == 7   # the whole month, from its first Sunday row
    assert nov["2026-11-10"]["entries"] == [] and nov["2026-11-24"]["entries"] == []    # the moved-away and the skipped date are just empty
    # the moved study is on its new day at its new time and place, and the day runs by the clock with the untimed item last
    assert [(e["title"], e["time"]) for e in nov["2026-11-11"]["entries"]] == [
        ("Lunch Bunch", "noon"), ("Choir Supper", "5pm"), ("Messy Bible Study", "6:30 p.m."), ("Coat Drive", "")]
    assert nov["2026-11-11"]["entries"][2] == {"title": "Messy Bible Study", "time": "6:30 p.m.", "location": "Parlor", "recurring": True}
    assert nov["2026-11-26"]["entries"] == [{"title": "37th Community Thanksgiving Meal", "time": "", "location": "", "recurring": False}]
    text = json.dumps(months)
    assert "painted" not in text and "Thanksgiving week" not in text and "event_id" not in text and "today" not in text


def test_times_sort_by_the_clock():
    order = ["8 a.m.", "9:30 AM", "10:45 a.m.", "noon", "12:15 p.m.", "1pm", "5pm", "6 p.m.", "6:30 p.m.", "after worship", ""]
    assert sorted(reversed(order), key=calendar_feed._time_key) == order


def test_site_data_carries_the_month_view_and_a_one_page_landscape_pdf_of_each_month(cal_store, tmp_path):
    from builder.pipeline import pdf
    (tmp_path / "src/_data").mkdir(parents=True)
    written = calendar_feed.write_site_files(cal_store, tmp_path)
    data = json.loads((tmp_path / "src/_data/calendarEvents.json").read_text())
    assert len(data["months"]) == calendar_feed.PUBLIC_MONTHS and data["events"] and data["months"][0]["weeks"]
    pdfs = sorted((tmp_path / calendar_feed.PRINT_DIR).glob("*.pdf"))
    assert len(pdfs) == calendar_feed.PUBLIC_MONTHS and set(pdfs) <= set(written)
    for m, path in zip(data["months"], pdfs):
        assert m["pdf"] == f"/calendar/print/{path.name}" and path.name.startswith(f"fpc-calendar-{m['month']}-")
        assert pdf.pdf_info(path) == {"pages": 1, "mediabox": [[0.0, 0.0, 792.0, 612.0]], "fonts": pdf.pdf_info(path)["fonts"]}  # letter, landscape
    # nothing changed: the same files, untouched; one event added: that month's file is replaced, the others stay
    stamps = {p.name: p.stat().st_mtime_ns for p in pdfs}
    calendar_feed.write_site_files(cal_store, tmp_path)
    assert {p.name: p.stat().st_mtime_ns for p in (tmp_path / calendar_feed.PRINT_DIR).glob("*.pdf")} == stamps
    first = data["months"][0]["month"]
    cal_store.upsert_event({"title": "Hanging of the Greens", "start_date": f"{first}-15"})
    calendar_feed.write_site_files(cal_store, tmp_path)
    after = sorted(p.name for p in (tmp_path / calendar_feed.PRINT_DIR).glob("*.pdf"))
    assert len(after) == calendar_feed.PUBLIC_MONTHS and after[0] != pdfs[0].name and after[1:] == [p.name for p in pdfs[1:]]


def _busy_month(tmp_path, per_day):
    """January 2027 (six rows of weeks) with ``per_day`` events on every day."""
    from datetime import date
    from builder.pipeline.store import Store
    s = Store(tmp_path / f"busy{per_day}.sqlite")
    for i in range(per_day):
        s.upsert_event({"title": f"Advent Workshop, Group {i + 1}", "start_date": "2026-11-01", "end_date": "2027-02-28", "time_text": "6:30 p.m."})
    months = calendar_feed.public_months(s, count=1, today=date(2027, 1, 10))
    s.close()
    return months


def test_a_crowded_month_is_scaled_down_to_stay_on_one_page(tmp_path):
    import re
    import pymupdf
    from builder.pipeline import pdf
    months = _busy_month(tmp_path, 4)   # more than six rows hold at full size
    (path,) = calendar_feed.write_month_pdfs(months, tmp_path)
    info = pdf.pdf_info(path)
    assert info["pages"] == 1 and info["mediabox"] == [[0.0, 0.0, 792.0, 612.0]]
    with pymupdf.open(path) as doc:
        page = doc[0]
        assert len(re.findall(r"Group\s+4\b", page.get_text())) == 42 and "as it stood on" in page.get_text()  # every day's last line, and the footer
        sizes = {round(sp["size"], 1) for b in page.get_text("dict")["blocks"] for ln in b.get("lines", []) for sp in ln["spans"] if "Group" in sp["text"]}
        assert sizes and max(sizes) < 8   # the 8 pt entries were shrunk to make room


def test_a_month_that_cannot_fit_readably_has_no_pdf_and_prints_from_the_browser(tmp_path):
    months = _busy_month(tmp_path, 30)
    assert calendar_feed.write_month_pdfs(months, tmp_path) == []
    assert "pdf" not in months[0] and not list((tmp_path / calendar_feed.PRINT_DIR).glob("*.pdf"))


def test_publish_takes_the_printable_months_once_there_are_some(tmp_path):
    from builder.pipeline import publish
    assert calendar_feed.PRINT_DIR not in publish.paths_for("2026-11-08", tmp_path)
    (tmp_path / calendar_feed.PRINT_DIR).mkdir(parents=True)
    (tmp_path / calendar_feed.PRINT_DIR / "fpc-calendar-2026-11-00000000.pdf").write_bytes(b"%PDF")
    assert calendar_feed.PRINT_DIR in publish.paths_for("2026-11-08", tmp_path)


def test_events_gain_the_exceptions_table_on_an_older_database(tmp_path):
    import sqlite3
    from builder.pipeline.store import Store
    path = tmp_path / "old.sqlite"
    s = Store(path)
    eid = s.upsert_event({"title": "Kids Quest", "start_date": "2026-10-21", "recurrence": "WEEKLY;BYDAY=WE"})
    s.close()
    db = sqlite3.connect(path)
    db.execute("drop table event_exceptions")
    db.commit(); db.close()
    s = Store(path)
    assert s.event(eid)["exceptions"] == [] and s.events()[0]["exceptions"] == []
    calendar_feed.skip_date(s, eid, "2026-11-25")
    assert [x["occurrence_date"] for x in s.events()[0]["exceptions"]] == ["2026-11-25"]
    s.close()
