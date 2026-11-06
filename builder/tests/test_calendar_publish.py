"""Keeping the public calendar current between bulletins: the calendar-only publish
(a scratch clone with a bare remote stands in for the repo and GitHub), the push that
follows an edit on the Calendar screen, and the screen's own account of it."""
import json
import os
import subprocess
from datetime import date

import pytest
from fastapi.testclient import TestClient

from builder import config
from builder.app import calendar_sync
from builder.pipeline import calendar_feed, publish
from builder.pipeline.store import Store


def _run(cmd, cwd):
    return subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture()
def clone(tmp_path, monkeypatch):
    """A clone on main with one pushed commit, its bare remote, and a store with two events."""
    remote, repo = tmp_path / "remote.git", tmp_path / "repo"
    _run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], tmp_path)
    _run(["git", "clone", "-q", str(remote), str(repo)], tmp_path)
    for k, v in (("user.email", "t@example.com"), ("user.name", "t")):
        _run(["git", "config", k, v], repo)
    (repo / "src/_data").mkdir(parents=True)
    (repo / "README").write_text("x")
    _run(["git", "add", "."], repo)
    _run(["git", "commit", "-q", "-m", "init"], repo)
    _run(["git", "push", "-q", "origin", "HEAD:refs/heads/main"], repo)
    monkeypatch.setattr(publish, "eleventy_check", lambda repo: "built")   # the site build is not what is tested here
    monkeypatch.setattr(calendar_feed, "write_month_pdfs", lambda months, repo, as_of=None: [])  # nor is Chromium
    monkeypatch.setattr(config, "VAR", tmp_path / "var")
    s = Store(tmp_path / "cal.sqlite")
    s.upsert_event({"title": "Messy Bible Study", "start_date": "2026-10-20", "time_text": "8 a.m.", "recurrence": "WEEKLY;BYDAY=TU"})
    s.upsert_event({"title": "37th Community Thanksgiving Meal", "start_date": "2026-11-26"})
    yield repo, remote, s
    s.close()


def _remote_log(remote):
    return _run(["git", "log", "--format=%s", "main"], remote).splitlines()


def test_site_files_do_not_change_until_the_calendar_does(clone):
    repo, _, s = clone
    calendar_feed.write_site_files(s, repo, today=date(2026, 11, 6))
    ics, data = (repo / "src/calendar.ics"), (repo / "src/_data/calendarEvents.json")
    before = (ics.read_bytes(), data.read_bytes(), ics.stat().st_mtime_ns, data.stat().st_mtime_ns)
    calendar_feed.write_site_files(s, repo, today=date(2026, 11, 29))   # weeks later, same month, nothing edited
    assert (ics.read_bytes(), data.read_bytes(), ics.stat().st_mtime_ns, data.stat().st_mtime_ns) == before
    listed = json.loads(data.read_text())
    assert listed["events"][0]["date"] == "2026-11-03" and listed["events"][-1]["date"] == "2027-01-26"   # from the 1st to the end of the last month in the grid
    calendar_feed.write_site_files(s, repo, today=date(2026, 12, 1))    # a new month: the page data moves on, the feed does not
    assert json.loads(data.read_text())["months"][0]["month"] == "2026-12" and ics.read_bytes() == before[0]
    s.upsert_event({"title": "Hanging of the Greens", "start_date": "2026-12-02"})
    calendar_feed.write_site_files(s, repo, today=date(2026, 12, 1))
    assert b"Hanging of the Greens" in ics.read_bytes() and "Hanging of the Greens" in data.read_text()


def test_calendar_publish_pushes_the_calendar_files_and_only_when_they_change(clone):
    repo, remote, s = clone
    (repo / "bulletins/2026-11-08").mkdir(parents=True)
    (repo / "bulletins/2026-11-08/week.json").write_text("{}")   # a week in progress, not to be published by this
    (repo / "README").write_text("edited, uncommitted")
    res = publish.publish_calendar(store=s, repo=repo, user="t@example.com")
    assert res["changed"] and res["pushed"]
    assert _remote_log(remote) == [publish.CALENDAR_SUBJECT, "init"]
    assert sorted(_run(["git", "show", "--name-only", "--format=", "main"], remote).splitlines()) == ["src/_data/calendarEvents.json", "src/calendar.ics"]
    assert "?? bulletins/2026-11-08/week.json" in _run(["git", "status", "--porcelain", "-uall"], repo)   # still untracked
    assert _run(["git", "diff", "--name-only"], repo) == "README"                                           # still an uncommitted edit
    assert s.last_audit("calendar.publish")["user_email"] == "t@example.com"
    # nothing edited: no commit, no push, nothing logged
    assert publish.publish_calendar(store=s, repo=repo) == {"changed": False}
    assert len(_remote_log(remote)) == 2
    # an edit goes out
    s.upsert_event({"title": "Session Meeting", "start_date": "2026-11-01", "recurrence": "MONTHLY;BYDAY=1SU"})
    assert publish.publish_calendar(store=s, repo=repo)["pushed"]
    assert len(_remote_log(remote)) == 3 and b"Session Meeting" in subprocess.run(["git", "show", "main:src/calendar.ics"], cwd=remote, capture_output=True).stdout


def test_calendar_publish_picks_up_what_was_pushed_from_elsewhere_first(clone, tmp_path):
    repo, remote, s = clone
    other = tmp_path / "other"
    _run(["git", "clone", "-q", str(remote), str(other)], tmp_path)
    for k, v in (("user.email", "o@example.com"), ("user.name", "o")):
        _run(["git", "config", k, v], other)
    (other / "about.txt").write_text("edited on GitHub")
    _run(["git", "add", "."], other); _run(["git", "commit", "-q", "-m", "elsewhere"], other); _run(["git", "push", "-q", "origin", "HEAD:refs/heads/main"], other)
    assert publish.publish_calendar(store=s, repo=repo)["pushed"]
    assert _remote_log(remote) == [publish.CALENDAR_SUBJECT, "elsewhere", "init"]


def test_calendar_publish_refuses_to_push_other_work_and_takes_back_a_commit_it_could_not_push(clone):
    repo, remote, s = clone
    (repo / "README").write_text("a local commit nobody pushed")
    _run(["git", "commit", "-q", "-am", "local only"], repo)
    with pytest.raises(publish.PublishError, match="never pushed"):
        publish.publish_calendar(store=s, repo=repo)
    assert _remote_log(remote) == ["init"]
    _run(["git", "reset", "-q", "--hard", "origin/main"], repo)
    _run(["git", "checkout", "-q", "-b", "trial"], repo)
    with pytest.raises(publish.PublishError, match="not main"):
        publish.publish_calendar(store=s, repo=repo)
    _run(["git", "checkout", "-q", "main"], repo)
    # the push itself fails: the commit is undone, and the next run publishes as if nothing had happened
    real = publish._git

    def no_push(r, *args, **kw):
        if args[0] == "push":
            raise publish.PublishError("git push failed: network")
        return real(r, *args, **kw)
    publish._git = no_push
    try:
        with pytest.raises(publish.PublishError, match="network"):
            publish.publish_calendar(store=s, repo=repo)
    finally:
        publish._git = real
    assert _run(["git", "log", "--format=%s"], repo).splitlines() == ["init"]
    assert publish.publish_calendar(store=s, repo=repo)["pushed"] and _remote_log(remote) == [publish.CALENDAR_SUBJECT, "init"]


def test_feed_stamps_are_the_events_own(clone):
    _, _, s = clone
    first = calendar_feed.build_ics(s)
    assert first == calendar_feed.build_ics(s)   # no clock in it
    stamp = s.events()[0]["updated_at"].replace("-", "").replace(":", "").replace("+0000", "Z")
    assert f"DTSTAMP:{stamp}".encode() in first and calendar_feed.validate_ics(first) == 2


# ── the push that follows an edit ────────────────────────────────────
def test_edits_are_gathered_into_one_send(monkeypatch):
    sent = []
    monkeypatch.setattr(config, "CALENDAR_AUTOPUBLISH", True)
    monkeypatch.setattr(config, "CALENDAR_PUSH_DELAY", 0.15)
    monkeypatch.setattr(publish, "publish_calendar", lambda store, user=None: sent.append(user) or {"changed": True, "commit": "abc1234"})
    import time
    for who in ("a", "b", "c"):
        calendar_sync.request(None, who)
        time.sleep(0.03)
    assert sent == [] and calendar_sync._due is not None
    time.sleep(0.4)
    assert sent == ["c"] and calendar_sync._due is None
    monkeypatch.setattr(config, "CALENDAR_AUTOPUBLISH", False)
    calendar_sync.request(None, "d")
    time.sleep(0.3)
    assert sent == ["c"]


@pytest.fixture(scope="module")
def client(tmp_var, store):
    os.environ.setdefault("SESSION_SECRET", "test-secret")
    from builder.app import main
    main._store = store
    c = TestClient(main.app, follow_redirects=False)
    c.cookies.clear()
    store.add_user("cal-publisher@example.com", None, "publisher")
    assert c.get("/auth/" + store.create_magic_link(store.user_by_email("cal-publisher@example.com")["id"])).status_code == 303
    return c


def test_saving_an_event_asks_for_a_send_and_the_screen_says_where_things_stand(client, store, monkeypatch):
    asked = []
    monkeypatch.setattr(calendar_sync, "request", lambda s, user=None: asked.append(user))
    r = client.post("/events", data={"title": "Choir Supper", "start_date": "2026-12-09"})
    assert r.status_code == 303 and asked == ["cal-publisher@example.com"]
    eid = next(e["id"] for e in store.events() if e["title"] == "Choir Supper")
    client.post(f"/events/{eid}", data={"title": "Choir Supper", "start_date": "2026-12-10"})
    client.post(f"/events/{eid}/delete")
    assert len(asked) == 3
    page = client.get("/events").text
    assert "The public calendar keeps up by itself" in page and "Send to the site now" in page and "every night and with each bulletin" in page  # auto is off under test

    monkeypatch.setattr(publish, "publish_calendar", lambda store, user=None: {"changed": True, "commit": "abc1234"})
    r = client.post("/calendar/send")
    assert r.status_code == 303 and "abc1234" in r.headers["location"]
    monkeypatch.setattr(publish, "publish_calendar", lambda store, user=None: {"changed": False})
    assert "already+matches" in client.post("/calendar/send").headers["location"]

    def boom(store, user=None):
        raise publish.PublishError("git push failed: network")
    monkeypatch.setattr(publish, "publish_calendar", boom)
    assert "was+not+sent" in client.post("/calendar/send").headers["location"]
    page = client.get("/events").text
    assert "did not go through" in page and "git push failed: network" in page
    client.cookies.clear()
    assert client.post("/calendar/send").status_code == 303 and client.post("/calendar/send").headers["location"].startswith("/login")
