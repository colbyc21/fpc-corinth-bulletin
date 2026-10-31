"""Phase 2 model steps, offline: pricing and caps, the parse and content
post-processing, and the fit loop's layout tool. No network."""
import json

import pytest

from builder import config
from builder.pipeline import content as contentmod, fit as fitmod, model, parse as parsemod
from builder.pipeline import week as weekmod


class _Usage:
    def __init__(self, i=0, w=0, r=0, o=0):
        self.input_tokens, self.cache_creation_input_tokens, self.cache_read_input_tokens, self.output_tokens = i, w, r, o


def test_price_uses_rate_card():
    u = model.usage_dict(_Usage(1_000_000, 1_000_000, 1_000_000, 1_000_000))
    assert model.price("claude-opus-5-5", u) == pytest.approx(4 + 5 + 0.20 + 20)
    assert model.price("claude-haiku-5-5", {"output_tokens": 2_000_000}) == pytest.approx(1.0)


def test_job_caps_and_kill_switch(store, monkeypatch):
    job = model.Job("fit", "2026-11-01", store)
    assert (job.max_calls, job.max_usd) == config.JOB_CAPS["fit"]
    job.check()
    job.record("claude-opus-5-5", {"output_tokens": 200_000})  # $4 > the $3 cap
    assert store.job_spend(job.id) == pytest.approx(4.0)
    with pytest.raises(model.SpendCap):
        job.check()
    job2 = model.Job("parse", "2026-11-01", store, max_calls=1)
    job2.record("claude-opus-5-5", {"output_tokens": 1})
    with pytest.raises(model.SpendCap):
        job2.check()
    monkeypatch.setattr(config, "MONTHLY_CAP_USD", 1.0)
    with pytest.raises(model.SpendCap):
        model.Job("content", "x", store).check()
    monkeypatch.setattr(config, "MONTHLY_CAP_USD", 1000.0)
    monkeypatch.setattr(config, "MODEL_CALLS", False)
    with pytest.raises(model.ModelOff):
        model.Job("content", "x", store).check()
    assert model.spend_status(store)["model_calls"] is False


def test_complete_records_ledger_and_rejects_refusal(store, monkeypatch):
    class Resp:
        def __init__(self, stop):
            self.model, self.stop_reason, self.usage = "claude-opus-5-5", stop, _Usage(1000, 0, 5000, 100)
            self.content = [type("T", (), {"type": "text", "text": '{"ok": true}'})()]
            self.stop_details = None

    calls = []

    class Stream:
        def __init__(self, stop):
            self.stop = stop

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get_final_message(self):
            return Resp(self.stop)

    class Beta:
        class messages:
            @staticmethod
            def stream(**kw):
                calls.append(kw)
                return Stream(kw.get("_stop", "end_turn"))

    class Client:
        beta = Beta()

    monkeypatch.setattr(model, "_client", Client())
    job = model.Job("parse", "t", store)
    resp = model.complete(job, system="sys", messages=[{"role": "user", "content": "hi"}], schema={"type": "object"})
    assert model.json_of(resp) == {"ok": True}
    assert job.calls == 1 and job.cost == pytest.approx((1000 * 4 + 5000 * 0.2 + 100 * 20) / 1e6)
    assert calls[0]["output_config"]["format"]["type"] == "json_schema" and calls[0]["system"][0]["cache_control"]
    assert store.ledger(1)[0]["job"] == job.id
    Beta.messages.stream = staticmethod(lambda **kw: Stream("refusal"))
    with pytest.raises(model.ModelError):
        model.complete(job, system="sys", messages=[{"role": "user", "content": "hi"}])


def test_parse_postprocess_adds_greetings_hymnal_check_and_pew_page(store):
    parsed = {
        "liturgy": {"items": [
            {"id": "gathering", "type": "section", "title": "Gathering", "star": False, "sub": False, "ref": None, "text": None, "lines": [],
             "silent_confession": False, "creed_note": None, "number": None, "piece": None, "credit": None, "name": None, "role": None, "scroll_only": False},
            {"id": "piano-prelude", "type": "plain", "title": "The Piano Prelude", "star": False, "sub": False, "ref": None, "text": None, "lines": [],
             "silent_confession": False, "creed_note": None, "number": None, "piece": None, "credit": None, "name": None, "role": None, "scroll_only": False},
            {"id": "meditation", "type": "scripture", "title": "Call to Meditation", "star": False, "sub": False, "ref": "Psalm 25:4-5",
             "text": '"Make me to know your ways"', "lines": [], "silent_confession": False, "creed_note": None, "number": None, "piece": None,
             "credit": None, "name": None, "role": None, "scroll_only": False},
            {"id": "hymn1", "type": "hymn", "title": "The Hymn", "star": True, "sub": False, "ref": None, "text": None, "lines": [],
             "silent_confession": False, "creed_note": None, "number": 345, "piece": "Leaning on the Everlasting Arms", "credit": None,
             "name": None, "role": None, "scroll_only": False},
            {"id": "reading", "type": "reading", "title": "Scripture Reading", "star": False, "sub": False, "ref": "Nehemiah 1:1-11", "text": None,
             "lines": [], "silent_confession": False, "creed_note": None, "number": None, "piece": None, "credit": None, "name": None, "role": None, "scroll_only": False},
        ], "notes": [{"for": "hymn1", "label": "Leaning on the Everlasting Arms:", "text": "A note."},
                     {"for": "nope", "label": "Lost", "text": "x"}]},
        "sermon": {"title": "SPIRITUAL RENEWAL: When God Begins to Work", "scripture": "Nehemiah 1:1-11", "preacher": "Rev. Waring Porter",
                   "intro": ["Hello"], "points": [{"heading": "I. God Awakens", "ref": "vv. 1-4", "quote": None, "sub": []}], "missing": False},
        "special": {"communion": False, "canvass": False},
        "flags": [],
    }
    out = parsemod.postprocess("2026-11-01", parsed, store)
    ids = [it["id"] for it in out["liturgy"]["items"]]
    assert ids[:3] == ["gathering", "piano-prelude", "greetings"]
    med = next(it for it in out["liturgy"]["items"] if it["id"] == "meditation")
    assert med["ref"] == "Psalm 25:4–5" and med["text"].startswith("“Make") and "star" in med and "number" not in med
    assert out["sermon"]["scripture"] == "Nehemiah 1:1–11" and out["sermon"]["points"][0]["ref"] == "vv. 1–4"
    assert out["pew_bible_page"] == 398
    msgs = " | ".join(f["message"] for f in out["flags"])
    assert "#345" in msgs and "#354" in msgs and "Blessed Assurance" in msgs
    assert "unknown item" in msgs
    assert [n["label"] for n in out["liturgy"]["notes"]] == ["Leaning on the Everlasting Arms"]
    weekmod.validate(weekmod.with_defaults({"date": "2026-11-01", "liturgy": out["liturgy"], "sermon": out["sermon"]}))


def test_parse_pew_page_lookup_by_chapter(store):
    table = store.standing("pew-bible-pages")
    assert parsemod.pew_bible_page("Nehemiah 1:5–11", table)[0] == 398
    page, why = parsemod.pew_bible_page("Revelation 21:1–5", table)
    assert page is None and "Revelation" in why


def test_content_postprocess_flags_and_names():
    parsed = {
        "subtitle": "Subtitle", "flowers": {"given": "in honor of X.", "message": None, "quote": None, "reference": None, "closing": None, "signed": "Y"},
        "announcements": [{"title": "Joy Riders Trip", "body": "The JoyRider trip is Thursday.", "print_suffix": None, "detail": None}],
        "serve": {"assisting_elder": "Pat Palmer", "greeters": None, "ushers": "A, B, & C", "acolytes": "D", "nursery": "E",
                  "circle_of_prayer": "Malechi Doran & Jack Griffith", "childrens_church": None, "av_engineer": "Russell Smith"},
        "calendar": [{"date": "2026-10-20", "items": ["Messy Bible Study, 8 a.m."]}, {"date": "Oct 22", "items": ["x"]}, {"date": "2026-10-01", "items": ["old"]}],
        "staff_changes": ["Jan Pike - Choir Director (new)"], "changes": [], "flags": [],
    }
    out = contentmod.postprocess("2026-10-18", parsed)
    assert out["subtitle_override"] == "Subtitle"  # the model decides placeholders; postprocess only trims
    assert out["serve"]["greeters"] is None and out["serve"]["circle_of_prayer"] == "Malechi Doren & Jack Griffith"
    assert out["announcements"][0]["body"] == "The Joy Riders trip is Thursday."
    assert [c["where"] for c in out["changes"]] == ["serve.circle_of_prayer", "announcements[0].body"]
    assert len(out["calendar"]) == 2
    wheres = [f["where"] for f in out["flags"]]
    assert "serve.greeters" in wheres and "calendar" in wheres and "staff" in wheres
    assert "serve.childrens_church" not in wheres


def test_fit_apply_layout_validates(store):
    week = weekmod.load_week("2026-11-01")
    layout = {"pages": week["layout"]["pages"], "page_style": {"p3": None, "p4": {"spread": False, "row_gap": "0.1in"}, "p5": None, "p6": None},
              "notes": [{"notes": ["prelude"], "at": "meditation", "span": 2, "align": "end"}], "overrides": []}
    new = fitmod.apply_layout(week, layout)
    assert new["layout"]["page_style"] == {"p4": {"spread": False, "row_gap": "0.1in"}} and new["layout"]["notes"][0]["span"] == 2
    bad = dict(layout, notes=[{"notes": ["nonexistent"], "at": "meditation", "span": 1, "align": "start"}])
    with pytest.raises(ValueError):
        fitmod.apply_layout(week, bad)
    bad2 = dict(layout, pages={**layout["pages"], "p6": layout["pages"]["p6"][:-1]})
    with pytest.raises(ValueError):
        fitmod.apply_layout(week, bad2)
    s = fitmod.summarize_week(week)
    assert s["items"][0]["id"] == "gathering" and any("note_words" in i for i in s["items"])


def test_fit_loop_with_fake_model(store, monkeypatch, tmp_path):
    """The loop applies the model's layout, keeps the best, and stops at the cap."""
    week = weekmod.load_week("2026-11-01")
    reports = iter([
        {"measurements": [{"label": "p4", "overflow_px": 30, "grid_overflow_px": 0, "content_past_bottom_px": 0}], "panels": {}, "overflow": [1]},
        {"measurements": [{"label": "p4", "overflow_px": 0, "grid_overflow_px": 0, "content_past_bottom_px": 0}], "panels": {}, "overflow": []},
        {"measurements": [{"label": "p4", "overflow_px": 0, "grid_overflow_px": 0, "content_past_bottom_px": 0}], "panels": {}, "overflow": []},
    ])
    monkeypatch.setattr(fitmod.buildmod, "build", lambda *a, **k: next(reports))
    saved = {}
    monkeypatch.setattr(fitmod.weekmod, "save_week", lambda w, path=None: saved.update(layout=w["layout"]))
    monkeypatch.setattr(fitmod.weekmod, "load_week", lambda d: week)

    class Block:
        def __init__(self, **kw):
            self.__dict__.update(kw)

        def model_dump(self, exclude_none=True):
            return {k: v for k, v in self.__dict__.items() if v is not None}

    turn = {"n": 0}

    def fake_complete(job, **kw):
        turn["n"] += 1
        job.record("claude-opus-5-5", {"output_tokens": 100})
        if turn["n"] == 1:
            layout = {"pages": week["layout"]["pages"], "page_style": {"p3": None, "p4": {"spread": False, "row_gap": "0.12in"}, "p5": None, "p6": None},
                      "notes": [{"notes": ["invocation", "confession-call"], "at": "invocation", "span": 2, "align": "start"}], "overrides": []}
            content = [Block(type="tool_use", id="t1", name="set_layout", input={"layout": layout}),
                       Block(type="tool_use", id="t2", name="render_and_measure", input={})]
            return Block(content=content, stop_reason="tool_use")
        return Block(content=[Block(type="text", text="Merged the two p4 notes.")], stop_reason="end_turn")

    monkeypatch.setattr(fitmod.model, "complete", fake_complete)
    res = fitmod.fit("2026-11-01", store=store)
    assert res["fitted"] and res["turns"] == 2 and "Merged" in res["summary"]
    assert saved["layout"]["notes"][0]["notes"] == ["invocation", "confession-call"]


def test_week_validate_rejects_bad_spans(store):
    week = weekmod.load_week("2026-11-01")
    ok = dict(week["layout"], notes=[{"notes": ["benediction"], "at": "benediction", "span": 3, "align": "start"}])
    weekmod.validate({**week, "layout": ok})
    hide = dict(week["layout"], notes=[{"notes": ["missionary"], "at": "missionary", "span": 5, "align": "start"}])  # covers children's note
    with pytest.raises(ValueError, match="hide the note"):
        weekmod.validate({**week, "layout": hide})
    cross = dict(week["layout"], notes=[{"notes": ["benediction"], "at": "commitment", "span": 3, "align": "end"}])  # crosses Sending
    with pytest.raises(ValueError, match="crosses"):
        weekmod.validate({**week, "layout": cross})
    long = dict(week["layout"], notes=[{"notes": ["benediction"], "at": "benediction", "span": 9, "align": "start"}])
    with pytest.raises(ValueError, match="past the end"):
        weekmod.validate({**week, "layout": long})


def test_fit_apply_layout_announcement_columns(store):
    week = weekmod.load_week("2026-11-01")
    base = {"pages": week["layout"]["pages"], "page_style": {"p3": None, "p4": None, "p5": None, "p6": None}, "notes": [], "overrides": []}
    new = fitmod.apply_layout(week, {**base, "announcement_columns": [[0, 2], [1]]})
    assert new["layout"]["announcement_columns"] == [[0, 2], [1]]
    with pytest.raises(ValueError, match="announcement_columns"):
        fitmod.apply_layout(week, {**base, "announcement_columns": [[0], [1]]})
    assert fitmod.apply_layout(week, {**base, "announcement_columns": None})["layout"]["announcement_columns"] is None


def test_parse_hymn_verse_selection_moves_to_title(store):
    item = {"id": "hymn2", "type": "hymn", "title": "Hymn of Response", "star": True, "sub": False, "ref": "vv. 1-3", "text": None, "lines": [],
            "silent_confession": False, "creed_note": None, "number": 8, "piece": "Praise to the Lord, the Almighty", "credit": None,
            "name": None, "role": None, "scroll_only": False}
    parsed = {"liturgy": {"items": [item], "notes": []}, "sermon": {"title": "T", "scripture": "Ezra 1:1", "preacher": "P", "intro": ["x"], "points": [], "missing": False},
              "special": {"communion": False, "canvass": False}, "flags": []}
    out = parsemod.postprocess("2026-10-25", parsed, store)
    h = next(i for i in out["liturgy"]["items"] if i["id"] == "hymn2")
    assert h["piece"] == "Praise to the Lord, the Almighty (vv. 1–3)" and "ref" not in h
