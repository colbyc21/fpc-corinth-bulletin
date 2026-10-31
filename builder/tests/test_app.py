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


class FakeAccess:
    """Stands in for the Cloudflare policy: an in-memory allowlist."""
    def __init__(self, emails=()):
        self.emails = sorted(emails)
        self.configured_flag = True
        self.fail = False

    def configured(self):
        return self.configured_flag

    def _check(self):
        if self.fail:
            raise RuntimeError("boom")

    def _gated(self, fn):
        """Like the real module: None when not configured, else the result."""
        if not self.configured_flag:
            return None
        self._check()
        return fn()

    def allowlist(self):
        return self._gated(lambda: list(self.emails))

    def add(self, email):
        def go():
            self.emails = sorted(set(self.emails) | {email}); return list(self.emails)
        return self._gated(go)

    def remove(self, email):
        def go():
            self.emails = [e for e in self.emails if e != email]; return list(self.emails)
        return self._gated(go)

    def sync(self, emails):
        def go():
            self.emails = sorted(set(emails)); return list(self.emails)
        return self._gated(go)


@pytest.fixture
def fake_access(monkeypatch):
    from builder.app import cfaccess, main
    fake = FakeAccess()
    for name in ("configured", "allowlist", "add", "remove", "sync"):
        monkeypatch.setattr(main.cfaccess, name, getattr(fake, name))
    monkeypatch.setattr(main.cfaccess, "AccessError", RuntimeError)
    return fake


def test_users_screen_keeps_access_allowlist_in_step(client, store, fake_access):
    login_as(client, store, "admin3@example.com", "admin")
    fake_access.emails = ["admin3@example.com"]
    r = client.post("/users", data={"email": "Editor@Example.com", "name": "Ed", "role": "editor"})
    assert r.status_code == 303 and "allowlist+updated" in r.headers["location"]
    assert "editor@example.com" in fake_access.emails
    uid = store.user_by_email("editor@example.com")["id"]
    r = client.post(f"/users/{uid}/delete")
    assert r.status_code == 303 and "allowlist+updated" in r.headers["location"]
    assert "editor@example.com" not in fake_access.emails
    assert store.user_by_email("editor@example.com") is None
    # the screen flags strays and gaps, and sync fixes both
    fake_access.emails = ["admin3@example.com", "stray@example.com"]
    html = client.get("/users").text
    assert "stray@example.com" in html and "Sync allowlist to users" in html
    r = client.post("/users/access/sync")
    assert r.status_code == 303
    assert fake_access.emails == sorted(u["email"] for u in store.users())
    assert "Sync allowlist to users" not in client.get("/users").text
    # an API failure is reported, not fatal, and the user row is still saved
    fake_access.fail = True
    r = client.post("/users", data={"email": "pub@example.com", "role": "publisher"})
    assert r.status_code == 303 and "Access+update+failed" in r.headers["location"]
    assert store.user_by_email("pub@example.com")["role"] == "publisher"
    assert "Could not read the Access allowlist" in client.get("/users").text
    fake_access.fail = False
    # unconfigured box: everything still works, with a plain note
    fake_access.configured_flag = False
    r = client.post("/users", data={"email": "ed2@example.com", "role": "editor"})
    assert "not+configured" in r.headers["location"]
    assert "not configured on this box" in client.get("/users").text
    client.cookies.clear()
