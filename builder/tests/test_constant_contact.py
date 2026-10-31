"""The Constant Contact client never touches the network in tests."""
import json

import pytest

from builder.pipeline import constant_contact as cc


def test_needs_client_id(monkeypatch):
    monkeypatch.delenv("CONSTANT_CONTACT_CLIENT_ID", raising=False)
    with pytest.raises(cc.CCError, match="CONSTANT_CONTACT_CLIENT_ID"):
        cc.client_id()


def test_device_flow_polls_then_saves(monkeypatch, tmp_path):
    monkeypatch.setenv("CONSTANT_CONTACT_CLIENT_ID", "abc")
    monkeypatch.setattr(cc, "TOKEN_FILE", tmp_path / "tok.json")
    answers = iter([{"error": "authorization_pending"}, {"access_token": "A", "refresh_token": "R", "expires_in": 28800, "scope": cc.SCOPES}])
    posted = []

    def fake_post(url, data):
        posted.append((url, data))
        return next(answers)

    monkeypatch.setattr(cc, "_post_form", fake_post)
    monkeypatch.setattr(cc.time, "sleep", lambda s: None)
    tok = cc.finish_device_flow({"device_code": "D", "interval": 1, "expires_in": 60})
    assert tok["access_token"] == "A" and posted[0][1]["grant_type"].endswith("device_code")
    saved = json.loads((tmp_path / "tok.json").read_text())
    assert saved["refresh_token"] == "R" and "obtained_at" in saved
    assert oct((tmp_path / "tok.json").stat().st_mode)[-3:] == "600"


def test_access_token_refreshes_when_stale(monkeypatch, tmp_path):
    monkeypatch.setenv("CONSTANT_CONTACT_CLIENT_ID", "abc")
    monkeypatch.setattr(cc, "TOKEN_FILE", tmp_path / "tok.json")
    cc.save_tokens({"access_token": "old", "refresh_token": "R1", "expires_in": 10})
    monkeypatch.setattr(cc.time, "time", lambda: cc.load_tokens()["obtained_at"] + 1000)
    monkeypatch.setattr(cc, "_post_form", lambda url, data: {"access_token": "new", "refresh_token": "R2", "expires_in": 28800} if data.get("grant_type") == "refresh_token" else {})
    assert cc.access_token() == "new" and cc.load_tokens()["refresh_token"] == "R2"


def test_campaign_body_shape(monkeypatch):
    seen = {}
    monkeypatch.setattr(cc, "call", lambda m, p, b=None, **k: seen.update({"method": m, "path": p, "body": b}) or {"campaign_activities": [{"role": "primary_email", "campaign_activity_id": "X"}]})
    camp = cc.create_campaign("Friday 2026-11-06", subject="The Friday Email", html="<p>hi</p>", from_name="FPC", from_email="office@fpccorinth.org", reply_to="office@fpccorinth.org", preheader="Hello")
    act = seen["body"]["email_campaign_activities"][0]
    assert seen["method"] == "POST" and seen["path"] == "/emails" and act["format_type"] == 5 and act["html_content"] == "<p>hi</p>" and act["preheader"] == "Hello"
    assert cc.primary_activity_id(camp) == "X"
