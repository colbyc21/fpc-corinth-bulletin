"""Phase 3: the volunteer form, the chat revise step (model faked), deploy status (HTTP faked)."""
import json

import pytest

from builder import config
from builder.pipeline import chat as chatmod, deploy_status, forms, model
from builder.pipeline import week as weekmod


def test_form_round_trip(store):
    week = weekmod.load_week("2026-11-01")
    form = {
        "subtitle_override": [""], "communion": ["on"], "welcome_variant": ["school_break"], "pew_bible_page": ["398"],
        "sermon_title": ["T"], "sermon_scripture": ["Nehemiah 1:1–11"], "sermon_preacher": [""],
        "flowers_given": ["in honor of X."], "flowers_signed": ["Y"], "flowers_message": [""], "flowers_quote": [""], "flowers_reference": [""], "flowers_closing": [""],
        "ann_title": ["A", "", "C"], "ann_body": ["body a", "", "body  c"], "ann_print_suffix": ["", "", "print only"], "ann_detail": ["", "", ""],
        "serve_assisting_elder": ["Dr. John Shipp"], "serve_greeters": [""], "serve_ushers": ["A, B, & C"],
        "calendar_lines": ["2026-11-03: Messy Bible Study, 8 a.m. · Election Day\nNov 5: First Thursday at the Youth House, noon\n"],
        "insert_thanksgiving-meal": ["on"],
    }
    w = forms.form_to_week(week, form)
    weekmod.validate(w)
    assert w["special"] == {"communion": True, "canvass": False} and w["welcome_variant"] == "school_break"
    assert w["sermon"]["preacher"] == "Rev. Waring Porter" and w["pew_bible_page"] == 398
    assert w["flowers"] == {"given": "in honor of X.", "signed": "Y", "message": None, "quote": None, "reference": None, "closing": None}
    assert [a["title"] for a in w["announcements"]] == ["A", "C"] and w["announcements"][1] == {"title": "C", "body": "body c", "print_suffix": "print only"}
    assert w["layout"]["announcement_columns"] is None  # count changed, so the back-cover split resets
    assert w["serve"] == {"assisting_elder": "Dr. John Shipp", "ushers": "A, B, & C"}
    assert w["calendar"] == [{"date": "2026-11-03", "items": ["Messy Bible Study, 8 a.m.", "Election Day"]},
                             {"date": "2026-11-05", "items": ["First Thursday at the Youth House, noon"]}]
    assert w["inserts"] == ["thanksgiving-meal"]
    assert forms.calendar_lines_text(w["calendar"]).splitlines()[0] == "2026-11-03: Messy Bible Study, 8 a.m. · Election Day"
    with pytest.raises(ValueError, match="title and a body"):
        forms.form_to_week(week, {**form, "ann_title": ["only title"], "ann_body": [""]})
    with pytest.raises(ValueError, match="calendar"):
        forms.form_to_week(week, {**form, "calendar_lines": ["no colon here"]})
    with pytest.raises(ValueError, match="Sermon"):
        forms.form_to_week(week, {**form, "sermon_title": [""]})


def test_chat_edit_week_pointer_and_protection(store):
    week = weekmod.load_week("2026-11-01")
    new, note = chatmod.apply_edit(week, "/serve/assisting_elder", '"Dr. John Shipp"')
    assert new["serve"]["assisting_elder"] == "Dr. John Shipp" and "set /serve" in note
    new, note = chatmod.apply_edit(week, "/serve/circle_of_prayer", "Jason & Jennifer Legoff")  # bare string, name rule
    assert new["serve"]["circle_of_prayer"] == "Jason & Jennifer LeGoff" and "LeGoff" in note
    new, _ = chatmod.apply_edit(week, "/calendar/-", '{"date": "2026-11-01", "items": ["Session Meeting, 12:15 p.m."]}')
    assert new["calendar"][-1]["items"] == ["Session Meeting, 12:15 p.m."]
    new, _ = chatmod.apply_edit(week, "/announcements/2", "__delete__")
    assert len(new["announcements"]) == len(week["announcements"]) - 1
    with pytest.raises(ValueError, match="not editable"):
        chatmod.apply_edit(week, "/layout/pages/p3", "[]")
    with pytest.raises(ValueError):
        chatmod.apply_edit(week, "/liturgy/items/0/type", '"bogus"')  # schema enum


def test_chat_revise_with_fake_model(store, monkeypatch):
    class Block:
        def __init__(self, **kw):
            self.__dict__.update(kw)

        def model_dump(self, exclude_none=True):
            return {k: v for k, v in self.__dict__.items() if v is not None}

    report = {"built_at": "x", "measurements": [{"label": "p3", "overflow_px": 0, "grid_overflow_px": 0, "content_past_bottom_px": 0}], "panels": {}, "overflow": []}
    monkeypatch.setattr(chatmod.buildmod, "last_report", lambda d: report)
    monkeypatch.setattr(chatmod.buildmod, "build", lambda *a, **k: report)
    saved = {"week": weekmod.load_week("2026-11-01")}
    monkeypatch.setattr(chatmod.weekmod, "save_week", lambda w, path=None: saved.update(week=w))
    monkeypatch.setattr(chatmod.weekmod, "load_week", lambda d: saved["week"])
    monkeypatch.setattr(chatmod.pdfmod, "Renderer", lambda: type("R", (), {"__enter__": lambda s: s, "__exit__": lambda s, *a: None})())
    turn = {"n": 0}

    def fake_complete(job, **kw):
        turn["n"] += 1
        job.record("claude-opus-5-5", {"output_tokens": 50})
        if turn["n"] == 1:
            return Block(stop_reason="tool_use", content=[Block(type="tool_use", id="a", name="edit_week", input={"path": "/serve/assisting_elder", "value": '"Dr. John Shipp"'}),
                                                           Block(type="tool_use", id="b", name="render_and_measure", input={})])
        return Block(stop_reason="end_turn", content=[Block(type="text", text="Changed the assisting elder to Dr. John Shipp; every page still fits.")])

    monkeypatch.setattr(chatmod.model, "complete", fake_complete)
    res = chatmod.revise("2026-11-01", "Change the assisting elder to Dr. John Shipp", store=store, user="ed@example.com")
    assert res["turns"] == 2 and "Dr. John Shipp" in res["reply"] and res["edits"] == ["set /serve/assisting_elder"]
    assert saved["week"]["serve"]["assisting_elder"] == "Dr. John Shipp"
    hist = store.chat_history("2026-11-01")
    assert [h["role"] for h in hist[-2:]] == ["user", "assistant"] and hist[-1]["cost_usd"] > 0
    # the per-bulletin message cap
    monkeypatch.setattr(chatmod, "MAX_MESSAGES_PER_WEEK", 1)
    with pytest.raises(model.SpendCap):
        chatmod.revise("2026-11-01", "again", store=store)


def test_deploy_status_live_marker(monkeypatch):
    pages = {f"{config.SITE_URL}/scroll/2026-11-01/": (200, '<html><meta name="fpc-build" content="20261031T170000"></html>')}
    monkeypatch.setattr(deploy_status, "_get", lambda url, headers=None: pages.get(url, (404, "")))
    monkeypatch.delenv("NETLIFY_TOKEN", raising=False)
    row = {"published_commit": "abc1234", "published_build": "20261031T170000"}
    assert deploy_status.status("2026-11-01", row)["state"] == "live"
    assert deploy_status.status("2026-11-01", {**row, "published_build": "20261031T180000"})["state"] == "building"
    assert deploy_status.status("2026-11-08", row)["state"] == "building"
    assert deploy_status.status("2026-11-01", {})["state"] == "unpublished"
    monkeypatch.setattr(deploy_status, "_get", lambda url, headers=None: (0, ""))
    assert deploy_status.status("2026-11-01", row)["state"] == "unknown"


def test_deploy_status_netlify(monkeypatch):
    monkeypatch.setenv("NETLIFY_TOKEN", "t")
    monkeypatch.setenv("NETLIFY_SITE_ID", "s")
    deploys = json.dumps([{"commit_ref": "abc1234def", "state": "error", "error_message": "Build script returned non-zero", "admin_url": "u"}])
    monkeypatch.setattr(deploy_status, "_get", lambda url, headers=None: (200, deploys) if "netlify" in url else (404, ""))
    s = deploy_status.status("2026-11-01", {"published_commit": "abc1234"})
    assert s["state"] == "failed" and "non-zero" in s["detail"] and s["source"] == "netlify"
