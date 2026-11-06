"""Site stats: what counts as a page view, how visitors are told apart without
keeping anything about them, and the beacon endpoint and Stats screen in the app."""
import json
import os
from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient

from builder import config
from builder.pipeline import stats
from builder.pipeline.store import Store

IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"
MAC = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15"
SUNDAY_1050 = datetime(2026, 11, 8, 16, 50, tzinfo=timezone.utc)   # 10:50 a.m. in Corinth
SUNDAY_EVENING = datetime(2026, 11, 9, 1, 30, tzinfo=timezone.utc)  # 7:30 p.m. Sunday in Corinth, already Monday in UTC
MONDAY = datetime(2026, 11, 9, 15, 0, tzinfo=timezone.utc)


@pytest.fixture()
def s(tmp_path):
    return Store(tmp_path / "stats.sqlite")


def test_paths_are_normalised_to_one_name_per_page():
    assert stats.normalize_path("/scroll/2026-11-08.html") == "/scroll/2026-11-08"
    assert stats.normalize_path("/scroll/2026-11-08/") == "/scroll/2026-11-08"
    assert stats.normalize_path("/scroll/2026-11-08/index.html") == "/scroll/2026-11-08"
    assert stats.normalize_path("/calendar/") == "/calendar"
    assert stats.normalize_path("/") == "/"
    for bad in (None, 7, "", "calendar", "/a b", "/x?y=1", "/../etc", "//evil.example", "/" + "a" * 200, "/<script>"):
        assert stats.normalize_path(bad) is None, bad


def test_referrer_keeps_only_an_outside_host():
    assert stats.referrer_host("https://www.fpccorinth.org/visit?x=1") == "fpccorinth.org"
    assert stats.referrer_host("https://bulletin.fpccorinth.org/archive/") is None  # a move within the site
    assert stats.referrer_host("") is None
    assert stats.referrer_host("javascript:alert(1)") is None
    assert stats.referrer_host(None) is None


def test_devices():
    assert stats.device_of(IPHONE) == "phone"
    assert stats.device_of(MAC) == "desktop"
    assert stats.device_of("Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148") == "tablet"
    assert stats.device_of("Mozilla/5.0 (Linux; Android 14; SM-X710) AppleWebKit/537.36 Chrome/126.0 Safari/537.36") == "tablet"
    assert stats.device_of("Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 Chrome/126.0 Mobile Safari/537.36") == "phone"


def test_record_counts_readers_and_drops_robots_and_bad_paths(s):
    assert stats.record(s, path="/scroll/2026-11-08/", ua=IPHONE, ip="10.0.0.1", now=SUNDAY_1050)
    assert not stats.record(s, path="/scroll/2026-11-08/", ua="Mozilla/5.0 (compatible; Googlebot/2.1)", ip="10.0.0.2", now=SUNDAY_1050)
    assert not stats.record(s, path="/scroll/2026-11-08/", ua="", ip="10.0.0.2", now=SUNDAY_1050)
    assert not stats.record(s, path="not a path", ua=IPHONE, ip="10.0.0.1", now=SUNDAY_1050)
    row = dict(s.db.execute("select * from hits").fetchone())
    assert (row["day"], row["hour"], row["path"], row["device"], row["referrer"]) == ("2026-11-08", 10, "/scroll/2026-11-08", "phone", None)
    assert "10.0.0.1" not in json.dumps(row) and "iPhone" not in json.dumps(row)  # neither the address nor the browser is kept


def test_a_visitor_is_one_device_once_a_day_and_cannot_be_followed_to_the_next(s):
    for _ in range(3):
        stats.record(s, path="/scroll/2026-11-08", ua=IPHONE, ip="10.0.0.1", now=SUNDAY_1050)
    stats.record(s, path="/calendar/", ua=IPHONE, ip="10.0.0.1", now=SUNDAY_EVENING)
    stats.record(s, path="/scroll/2026-11-08", ua=IPHONE, ip="10.0.0.2", now=SUNDAY_1050)   # the next pew
    stats.record(s, path="/scroll/2026-11-08", ua=MAC, ip="10.0.0.1", now=SUNDAY_1050)      # same wifi, another device
    assert s.hits_total("2026-11-08", "2026-11-08") == {"views": 6, "visitors": 3}
    sunday_ids = {r["visitor"] for r in s.db.execute("select visitor from hits")}
    stats.record(s, path="/scroll/2026-11-08", ua=IPHONE, ip="10.0.0.1", now=MONDAY)
    monday_id = s.db.execute("select visitor from hits where day='2026-11-09'").fetchone()["visitor"]
    assert monday_id not in sunday_ids
    assert [r["day"] for r in s.db.execute("select day from stats_salt")] == ["2026-11-09"]  # Sunday's salt is gone


def test_a_flood_from_one_visitor_stops_counting(s, monkeypatch):
    monkeypatch.setattr(stats, "MAX_PER_VISITOR_PER_DAY", 5)
    counted = [stats.record(s, path="/about", ua=MAC, ip="10.0.0.9", now=MONDAY) for _ in range(8)]
    assert counted == [True] * 5 + [False] * 3
    assert stats.record(s, path="/about", ua=MAC, ip="10.0.0.10", now=MONDAY)  # someone else still counts


def test_summary(s):
    for ip in ("1", "2", "3"):
        stats.record(s, path="/scroll/2026-11-08/", ua=IPHONE, ip=ip, now=SUNDAY_1050)
    stats.record(s, path="/scroll/2026-11-08/", ua=MAC, ip="4", now=SUNDAY_EVENING, referrer="https://www.fpccorinth.org/")
    stats.record(s, path="/scroll/2026-11-08/", ua=MAC, ip="4", now=MONDAY)
    stats.record(s, path="/hymns/", ua=MAC, ip="4", now=MONDAY)
    st = stats.summary(s, today=date(2026, 11, 10))
    assert st["week"] == {"views": 6, "visitors": 5} and st["week_delta"]["views"] is None
    assert st["last_sunday"] == "2026-11-08"
    assert st["sunday"] == {"date": "2026-11-08", "views": 5, "visitors": 5, "sunday_views": 4, "sunday_visitors": 4, "service_visitors": 3}
    assert len(st["days"]) == 56 and st["days"][-1]["date"] == "2026-11-10"
    by_day = {d["date"]: d for d in st["days"]}
    assert by_day["2026-11-08"]["views"] == 4 and by_day["2026-11-08"]["tick"] == "Nov 8" and by_day["2026-11-09"]["tick"] == ""
    assert st["axis_max"] == 4 and st["ticks"] == [0, 1, 2, 3, 4]
    assert [(p["label"], p["views"]) for p in st["pages"]] == [("Bulletin, November 8", 5), ("Hymn Index", 1)]
    assert [(r["label"], r["views"]) for r in st["referrers"]] == [("fpccorinth.org", 1)]
    assert {d["label"]: d["views"] for d in st["devices"]} == {"Phone": 3, "Computer": 3}
    assert stats.summary(Store(s.path.with_name("empty.sqlite")))["empty"]


def test_axis_tops_are_round():
    assert [stats._nice_max(n) for n in (0, 1, 4, 5, 9, 37, 40, 41, 180, 950)] == [4, 4, 4, 8, 20, 40, 40, 80, 200, 2000]


# ── the app ──────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def client(tmp_var, store):
    os.environ.setdefault("SESSION_SECRET", "test-secret")
    from builder.app import main
    main._store = store
    c = TestClient(main.app, follow_redirects=False)
    c.cookies.clear()
    return c


def beacon(client, path="/scroll/2026-11-08/", origin=config.SITE_URL, ua=IPHONE, **extra):
    return client.post("/hit", content=json.dumps({"p": path, "r": "", **extra}),
                       headers={"origin": origin, "user-agent": ua, "content-type": "text/plain;charset=UTF-8", "cf-connecting-ip": "203.0.113.7"})


def hits(store):
    return store.db.execute("select count(*) as n from hits").fetchone()["n"]


def test_beacon_counts_a_view_from_the_site_only(client, store):
    before = hits(store)
    r = beacon(client)
    assert r.status_code == 204 and r.headers["access-control-allow-origin"] == config.SITE_URL
    assert hits(store) == before + 1
    assert beacon(client, origin="https://example.com").status_code == 204          # another site
    assert beacon(client, ua="Mozilla/5.0 (compatible; bingbot/2.0)").status_code == 204  # a robot
    assert beacon(client, path="/" + "x" * 3000).status_code == 204                 # too big to read
    assert client.post("/hit", content="not json", headers={"origin": config.SITE_URL, "user-agent": IPHONE}).status_code == 204
    assert client.post("/hit", content="[1]", headers={"origin": config.SITE_URL, "user-agent": IPHONE}).status_code == 204
    assert hits(store) == before + 1


def test_signed_in_builders_are_not_counted_and_see_the_stats(client, store):
    assert client.get("/stats").status_code == 303  # behind the login like everything else
    store.add_user("stats@example.com", None, "editor")
    token = store.create_magic_link(store.user_by_email("stats@example.com")["id"])
    assert client.get(f"/auth/{token}").status_code == 303
    before = hits(store)
    assert beacon(client).status_code == 204
    assert hits(store) == before
    r = client.get("/stats")
    assert r.status_code == 200 and "Views per day" in r.text and "By bulletin" in r.text and 'href="/stats"' in r.text
    client.cookies.clear()
