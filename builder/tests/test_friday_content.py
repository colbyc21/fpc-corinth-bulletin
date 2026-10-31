"""The Friday-email model step is faked; the merge with the tables is real."""
import json

from builder.pipeline import friday, friday_content as fc, model


def _parsed(**over):
    base = {
        "scripture": {"text": "Verse.", "ref": "Acts 10:43"}, "subject": None, "preheader": None,
        "sections": [
            {"key": "women", "label": None, "title": None, "intro": "Jump in!", "footer_line": None, "items": [
                {"eyebrow": None, "title": "First Thursday", "when": "Thursday, November 5 · Noon · Youth House", "when_highlight": True,
                 "body": ["Jennifer Legoff will host."], "note": None, "quote": None, "link": None, "image": None, "image_position": "after_body"}]},
            {"key": "prepare", "label": None, "title": None, "intro": None, "footer_line": None, "items": [
                {"eyebrow": None, "title": None, "when": None, "when_highlight": False, "body": [], "note": None, "quote": None, "link": None, "image": None, "image_position": "after_body"}]},
        ],
        "birthdays": [{"date": "2026-11-02", "names": ["B. Member"]}, {"date": "bad", "names": ["X"]}],
        "anniversaries": [],
        "serve_lines": [{"role": "Assisting Elder", "names": "Dr. Don Shipp"}, {"role": "Nursery", "names": ""},
                        {"role": None, "names": "Infant (Room 4) — Laura Kate"}, {"role": "Joy in Bloom Deliverer (November)", "names": "Gloria Williams"}],
        "calendar_lines": [{"date": "2026-11-03", "items": ["Messy Bible Study at 8 am"]}, {"date": "2026-11-07", "items": ["Tabitha’s Toolbox Work Day"]}],
        "closing_hymn": {"lines": ["A line"], "attribution": "Someone"},
        "carried_forward": ["WM Book Club"], "dropped": ["Fall Festival"],
        "changes": [], "flags": [],
    }
    base.update(over)
    return base


def test_merge_uses_the_tables_and_flags_the_notes(store):
    from builder.pipeline import week as weekmod
    week = weekmod.load_week("2026-11-01")
    doc = fc.merge("2026-11-01", _parsed(), store=store, week=week)
    friday.validate(doc)
    # sections come back in the standing order, the empty item is dropped
    assert [s["key"] for s in doc["sections"]] == ["women"] and doc["sections"][0]["intro"] == "Jump in!"
    # name rule applied and recorded
    assert doc["sections"][0]["items"][0]["body"] == ["Jennifer LeGoff will host."]
    assert any(c["before"] == "Legoff" for c in doc["copyedit"]["changes"])
    # the roster wins over the notes and the disagreement is flagged; nursery rooms and the deliverer are added
    roles = [l["role"] for l in doc["serve"]]
    assert doc["serve"][0] == {"role": "Assisting Elder", "names": store.serve_for("2026-11-01")["assisting_elder"]}
    assert any("roster table says" in f["message"] for f in doc["copyedit"]["flags"])
    assert "Nursery" in roles and {"role": None, "names": "Infant (Room 4) — Laura Kate"} in doc["serve"]
    assert roles[-1] == "Joy in Bloom Deliverer (November)"
    # the calendar table's lines are kept; the notes' extra event is added and flagged, the duplicate is not
    nov3 = next(c for c in doc["calendar"] if c["date"] == "2026-11-03")
    assert nov3["items"].count("Messy Bible Study, 8 a.m.") == 1 and not any("8 am" in i for i in nov3["items"])
    assert any(c["date"] == "2026-11-07" and "Tabitha’s Toolbox Work Day" in c["items"] for c in doc["calendar"])
    assert any("not in the calendar table" in f["message"] for f in doc["copyedit"]["flags"])
    # dates: the bad birthday is flagged and dropped; carried/dropped become flags
    assert doc["birthdays"] == [{"date": "2026-11-02", "names": ["B. Member"]}]
    msgs = " ".join(f["message"] for f in doc["copyedit"]["flags"])
    assert "Unreadable date" in msgs and "Carried forward" in msgs and "Dropped as past" in msgs
    assert "Those Who Serve this Sunday" in friday.render(doc)


def test_compose_sends_notes_previous_and_tables(store, monkeypatch):
    seen = {}

    def fake_complete(job, **kw):
        seen.update(kw)
        job.record("claude-opus-5-5", {"input_tokens": 10, "output_tokens": 10})

        class R:
            content = [type("T", (), {"type": "text", "text": json.dumps(_parsed())})()]
        return R()

    monkeypatch.setattr(model, "complete", fake_complete)
    prev = friday.load("2026-11-01")
    from builder.pipeline import week as weekmod
    out = fc.compose("2026-11-08", "THE FRIDAY EMAIL\nOpening Scripture\n...", store=store, previous=prev, week=weekmod.load_week("2026-11-01"))
    msg = seen["messages"][0]["content"]
    assert "The office's notes" in msg and "Last week's email (2026-10-30)" in msg and "[women] First Thursday" in msg
    assert "roster table" in msg and "calendar table" in msg and seen["schema"] is fc.OUTPUT_SCHEMA
    assert "Worked example" in seen["system"] and "Confirmed name spellings" in seen["system"]
    assert out["_job"]["calls"] == 1


def test_example_round_trips_through_the_model_view():
    doc = friday.load("2026-11-01")
    view = fc._model_view(doc)
    import jsonschema
    jsonschema.Draft202012Validator(fc.OUTPUT_SCHEMA).validate({**view, "carried_forward": [], "dropped": [], "changes": [], "flags": []})
    assert fc.example_text() and "Worked example" in fc.example_text()
