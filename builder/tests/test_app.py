"""The web app behind its login: every route redirects anonymous users, the
magic-link flow signs in, roles gate the admin screens, and each screen renders."""
import os
import re

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client(tmp_var, store):
    os.environ.setdefault("SESSION_SECRET", "test-secret")
    os.environ.pop("RESEND_API_KEY", None)
    from builder.app import main
    main._store = store
    return TestClient(main.app, follow_redirects=False)


def login_as(client, store, email, role):
    store.add_user(email, None, role)
    uid = store.user_by_email(email)["id"]
    token = store.create_magic_link(uid)
    r = client.get(f"/auth/{token}")
    assert r.status_code == 303 and r.headers["location"] == "/"
    return client


def test_anonymous_is_redirected_everywhere(client):
    for path in ("/", "/weeks/2026-11-01", "/rosters", "/events", "/users", "/audit", "/standing", "/weeks/2026-11-01/pdf"):
        r = client.get(path)
        assert r.status_code == 303 and r.headers["location"].startswith("/login"), path
    assert client.get("/healthz").status_code == 200
    assert client.get("/login").status_code == 200


def test_login_request_is_uniform_and_rate_limited(client, store):
    r = client.post("/login", data={"email": "nobody@example.com"})
    assert r.status_code == 200 and "on its way" in r.text
    for _ in range(6):
        client.post("/login", data={"email": "flood@example.com"})
    r = client.post("/login", data={"email": "flood@example.com"})
    assert "Too many" in r.text


def test_magic_link_is_single_use(client, store):
    store.add_user("once@example.com", None, "editor")
    token = store.create_magic_link(store.user_by_email("once@example.com")["id"])
    assert client.get(f"/auth/{token}").status_code == 303
    r = client.get(f"/auth/{token}")
    assert r.status_code == 200 and "expired" in r.text
    client.cookies.clear()


def test_editor_sees_weeks_but_not_admin_screens(client, store):
    login_as(client, store, "editor@example.com", "editor")
    assert client.get("/").status_code == 200
    r = client.get("/weeks/2026-11-01")
    assert r.status_code == 200 and "week.json" in r.text and "Publishing needs the publisher role" in r.text
    for path in ("/rosters", "/events", "/users", "/audit", "/standing"):
        assert client.get(path).status_code == 403, path
    assert client.post("/weeks/2026-11-01/publish").status_code == 403
    client.cookies.clear()


def test_publisher_edits_calendar_but_not_other_admin_screens(client, store):
    login_as(client, store, "pub@example.com", "publisher")
    r = client.get("/events")
    assert r.status_code == 200 and 'href="/events"' in r.text and 'href="/users"' not in r.text
    r = client.post("/events", data={"title": "Bake sale", "start_date": "2026-12-05"}, follow_redirects=False)
    assert r.status_code == 303 and "Added" in r.headers["location"]
    for path in ("/rosters", "/users", "/audit", "/standing"):
        assert client.get(path).status_code == 403, path
    client.cookies.clear()


def test_admin_screens_render_and_edit(client, store):
    login_as(client, store, "admin@example.com", "admin")
    for path in ("/", "/weeks/2026-11-01", "/rosters", "/events", "/events?all=1", "/users", "/audit", "/standing", "/standing/welcome", "/calendar.ics"):
        r = client.get(path)
        assert r.status_code == 200, (path, r.status_code)
    r = client.post("/rosters/2026-11-08", data={"assisting_elder": "Dr. Steve Blair", "ushers": "A, B, & C"})
    assert r.status_code == 303
    assert store.serve_for("2026-11-08")["assisting_elder"] == "Dr. Steve Blair"
    r = client.post("/events", data={"title": "Test Event", "start_date": "2026-12-01", "time_text": "6 p.m.", "byday": "", "featured": "on"})
    assert r.status_code == 303 and "Added" in r.headers["location"]
    ev = [e for e in store.events() if e["title"] == "Test Event"][0]
    assert ev["featured"] == 1
    r = client.post(f"/events/{ev['id']}/delete")
    assert r.status_code == 303
    assert not [e for e in store.events() if e["title"] == "Test Event"]
    r = client.post("/standing/welcome", data={"raw": "{not json"})
    assert "Not+saved" in r.headers["location"]
    r = client.post("/users", data={"email": "pastor@example.com", "name": "Pastor", "role": "publisher"})
    assert r.status_code == 303 and store.user_by_email("pastor@example.com")["role"] == "publisher"
    client.cookies.clear()


def test_week_json_save_validates(client, store):
    login_as(client, store, "admin2@example.com", "admin")
    r = client.post("/weeks/2026-11-01/json", data={"raw": '{"date": "2026-11-01"}'})
    assert "Not+saved" in r.headers["location"]
    client.cookies.clear()


def test_week_page_form_refresh_and_chat_routes(client, store, monkeypatch, tmp_path):
    # work on a copy of the week folder so the committed week.json is never touched
    import shutil
    from builder import config
    bdir = tmp_path / "bulletins"
    shutil.copytree(config.BULLETINS_DIR / "2026-11-01", bdir / "2026-11-01")
    monkeypatch.setattr(config, "BULLETINS_DIR", bdir)
    login_as(client, store, "ed3@example.com", "editor")
    html = client.get("/weeks/2026-11-01").text
    assert "1 · Inputs" in html and 'name="serve_assisting_elder"' in html and 'name="calendar_lines"' in html
    data = {"sermon_title": "T2", "sermon_scripture": "Nehemiah 1:1–11", "sermon_preacher": "", "subtitle_override": "", "welcome_variant": "regular",
            "pew_bible_page": "", "flowers_given": "", "ann_title": ["One"], "ann_body": ["Body one."], "serve_assisting_elder": "Dr. John Shipp",
            "calendar_lines": "2026-11-03: Messy Bible Study, 8 a.m."}
    r = client.post("/weeks/2026-11-01/form", data=data)
    assert r.status_code == 303 and "Saved" in r.headers["location"]
    from builder.pipeline import week as weekmod
    w = weekmod.load_week("2026-11-01")
    assert w["sermon"]["title"] == "T2" and w["announcements"] == [{"title": "One", "body": "Body one."}] and w["flowers"] is None
    r = client.post("/weeks/2026-11-01/form", data={**data, "calendar_lines": "bad line"})
    assert "Not+saved" in r.headers["location"]
    r = client.post("/weeks/2026-11-01/refresh/serve")
    assert r.status_code == 303 and "Refreshed" in r.headers["location"]
    r = client.post("/weeks/2026-11-01/refresh/calendar")
    assert r.status_code == 303
    r = client.get("/weeks/2026-11-01/deploy.json")
    assert r.status_code == 200 and r.json()["state"] in ("unpublished", "building", "live", "unknown")
    from builder.app import main
    monkeypatch.setattr(main.chatmod, "revise", lambda d, m, store, user=None: {"reply": "ok", "cost": 0.01, "turns": 1, "report": None, "edits": []})
    r = client.post("/weeks/2026-11-01/chat", data={"message": "Change x"})
    assert r.status_code == 303 and "Done" in r.headers["location"]
    client.cookies.clear()
