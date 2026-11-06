"""SQLite store: rosters, events, standing text, users, audit log, week status, site stats.

One small database under ``BUILDER_VAR``. The standing JSON files in
``builder/data/standing`` and the CSVs in ``builder/data/seed`` are the
seed; the tables are what the app edits. ``Store.seed_if_empty`` loads them
the first time the app starts on a box.
"""
from __future__ import annotations

import csv
import json
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from builder import config

ROSTER_ROLES = [
    "assisting_elder", "greeters", "ushers", "acolytes",
    "nursery", "circle_of_prayer", "childrens_church", "av_engineer",
]
ROLE_LABELS = {
    "assisting_elder": "Assisting Elder", "greeters": "Greeters", "ushers": "Ushers",
    "acolytes": "Acolytes", "nursery": "Nursery", "circle_of_prayer": "Circle of Prayer",
    "childrens_church": "Children’s Church", "av_engineer": "AV Engineer",
}
# Kept on the roster but not printed under Those Who Serve: the flowers show in
# the bulletin through the dedication, so the name here is for whoever builds it.
ROSTER_EXTRAS = ["flowers"]
ROSTER_COLUMNS = ROSTER_ROLES + ROSTER_EXTRAS
ROSTER_LABELS = {**ROLE_LABELS, "flowers": "Flowers"}
USER_ROLES = ("editor", "publisher", "admin")

SCHEMA = """
create table if not exists rosters (
  sunday text primary key,
  assisting_elder text, greeters text, ushers text, acolytes text,
  nursery text, circle_of_prayer text, childrens_church text, av_engineer text,
  flowers text,
  updated_at text not null
);
create table if not exists events (
  id integer primary key autoincrement,
  uid text not null unique,
  title text not null,
  start_date text not null,
  end_date text,
  time_text text,
  location text,
  notes text,
  category text,
  recurrence text,          -- e.g. WEEKLY;BYDAY=TU or MONTHLY;BYDAY=1SU  (null = one-off)
  until_date text,          -- last date a recurring event occurs
  featured integer not null default 0,  -- list in every bulletin until it passes
  created_at text not null,
  updated_at text not null,
  deleted_at text
);
create index if not exists events_start on events(start_date);
create table if not exists event_exceptions (
  id integer primary key autoincrement,
  event_id integer not null references events(id),
  occurrence_date text not null,   -- the date the repeat rule lands on
  kind text not null check(kind in ('skip','move')),
  new_date text,                   -- move: the date it happens instead (the same day when only the time or place changes)
  time_text text,                  -- move: its time that day, if different
  location text,                   -- move: its place that day, if different
  note text,                       -- why; shown in the app, never printed
  created_at text not null,
  updated_at text not null,
  unique(event_id, occurrence_date)
);
create table if not exists standing (
  key text primary key,
  json text not null,
  updated_at text not null
);
create table if not exists users (
  id integer primary key autoincrement,
  email text not null unique,
  name text,
  role text not null check(role in ('editor','publisher','admin')),
  created_at text not null,
  last_login_at text,
  deleted_at text
);
create table if not exists magic_links (
  token text primary key,
  user_id integer not null references users(id),
  created_at text not null,
  expires_at text not null,
  used_at text
);
create table if not exists audit (
  id integer primary key autoincrement,
  ts text not null,
  user_email text,
  action text not null,
  detail text
);
create table if not exists weeks (
  date text primary key,
  status text not null default 'draft',   -- draft | built | ready | published
  built_at text, published_at text, published_commit text,
  updated_at text not null
);
create table if not exists chat (
  id integer primary key autoincrement,
  date text not null, ts text not null,
  role text not null,          -- user | assistant
  user_email text, text text not null, cost_usd real
);
create table if not exists ledger (
  id integer primary key autoincrement,
  ts text not null,
  job text, kind text, model text,
  input_tokens integer, cache_write_tokens integer, cache_read_tokens integer, output_tokens integer,
  cost_usd real not null
);
create table if not exists hits (
  id integer primary key autoincrement,
  ts text not null,            -- UTC
  day text not null,           -- the date in Corinth when the page was opened
  hour integer not null,       -- the hour there, 0-23
  path text not null,          -- normalised: /scroll/2026-11-08, /calendar, ...
  visitor text not null,       -- hash of address + browser under that day's salt; cannot be linked across days
  referrer text,               -- host of an outside page that linked here
  device text                  -- phone | tablet | desktop
);
create index if not exists hits_day on hits(day);
create table if not exists stats_salt (
  day text primary key,        -- only the current day's row is kept
  salt text not null
);
"""


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class Store:
    def __init__(self, path: Path | str | None = None):
        self.path = Path(path or config.DB_PATH)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("pragma journal_mode=wal")
        self.db.execute("pragma foreign_keys=on")
        self.db.executescript(SCHEMA)
        cols = {r["name"] for r in self.db.execute("pragma table_info(weeks)")}
        if "published_build" not in cols:
            self.db.execute("alter table weeks add column published_build text")
        have = {r["name"] for r in self.db.execute("pragma table_info(rosters)")}
        for col in ROSTER_EXTRAS:
            if col not in have:
                self.db.execute(f"alter table rosters add column {col} text")

    def close(self) -> None:
        self.db.close()

    # ── rosters ──────────────────────────────────────────────────────
    def roster(self, sunday: str) -> dict | None:
        row = self.db.execute("select * from rosters where sunday=?", (sunday,)).fetchone()
        return dict(row) if row else None

    def rosters(self, limit: int = 60) -> list[dict]:
        rows = self.db.execute("select * from rosters order by sunday desc limit ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def upsert_roster(self, sunday: str, values: dict) -> None:
        cols = ", ".join(ROSTER_COLUMNS)
        marks = ", ".join("?" for _ in ROSTER_COLUMNS)
        sets = ", ".join(f"{c}=excluded.{c}" for c in ROSTER_COLUMNS)
        vals = [(values.get(c) or "").strip() or None for c in ROSTER_COLUMNS]
        self.db.execute(
            f"insert into rosters (sunday, {cols}, updated_at) values (?, {marks}, ?) "
            f"on conflict(sunday) do update set {sets}, updated_at=excluded.updated_at",
            [sunday, *vals, now()],
        )

    def serve_for(self, sunday: str) -> dict:
        row = self.roster(sunday) or {}
        return {r: row.get(r) for r in ROSTER_ROLES}

    def flowers_for(self, sunday: str) -> str | None:
        """Who has the flowers that Sunday, from the roster (not a printed role)."""
        return (self.roster(sunday) or {}).get("flowers")

    # ── events ───────────────────────────────────────────────────────
    def events(self, include_deleted: bool = False) -> list[dict]:
        """Every event, each with ``exceptions``: the single dates of a repeating event that were skipped or moved."""
        sql = "select * from events" + ("" if include_deleted else " where deleted_at is null") + " order by start_date, id"
        rows = [dict(r) for r in self.db.execute(sql).fetchall()]
        by_event: dict[int, list[dict]] = {}
        for x in self.db.execute("select * from event_exceptions order by occurrence_date"):
            by_event.setdefault(x["event_id"], []).append(dict(x))
        for r in rows:
            r["exceptions"] = by_event.get(r["id"], [])
        return rows

    def event(self, event_id: int) -> dict | None:
        row = self.db.execute("select * from events where id=?", (event_id,)).fetchone()
        if not row:
            return None
        ev = dict(row)
        ev["exceptions"] = [dict(x) for x in self.db.execute(
            "select * from event_exceptions where event_id=? order by occurrence_date", (event_id,))]
        return ev

    def set_exception(self, event_id: int, occurrence_date: str, kind: str, new_date: str | None = None,
                      time_text: str | None = None, location: str | None = None, note: str | None = None) -> None:
        """Skip or move one date of a repeating event; a second call for the same date replaces the first."""
        if kind not in ("skip", "move"):
            raise ValueError("an exception is a skip or a move")
        if kind == "move" and not new_date:
            raise ValueError("a moved date needs the date it moves to")
        clean = [(v or "").strip() or None for v in (time_text, location, note)]
        ts = now()
        self.db.execute(
            "insert into event_exceptions (event_id, occurrence_date, kind, new_date, time_text, location, note, created_at, updated_at) "
            "values (?, ?, ?, ?, ?, ?, ?, ?, ?) on conflict(event_id, occurrence_date) do update set kind=excluded.kind, "
            "new_date=excluded.new_date, time_text=excluded.time_text, location=excluded.location, note=excluded.note, updated_at=excluded.updated_at",
            (event_id, occurrence_date, kind, new_date if kind == "move" else None, *(clean if kind == "move" else [None, None, clean[2]]), ts, ts),
        )
        self.db.execute("update events set updated_at=? where id=?", (ts, event_id))  # subscribers see LAST-MODIFIED move

    def clear_exception(self, event_id: int, occurrence_date: str) -> bool:
        cur = self.db.execute("delete from event_exceptions where event_id=? and occurrence_date=?", (event_id, occurrence_date))
        if cur.rowcount:
            self.db.execute("update events set updated_at=? where id=?", (now(), event_id))
        return bool(cur.rowcount)

    def upsert_event(self, data: dict, event_id: int | None = None) -> int:
        fields = ("title", "start_date", "end_date", "time_text", "location", "notes",
                  "category", "recurrence", "until_date", "featured")
        vals = {f: data.get(f) for f in fields}
        for f in ("end_date", "time_text", "location", "notes", "category", "recurrence", "until_date"):
            if isinstance(vals[f], str) and not vals[f].strip():
                vals[f] = None
        vals["featured"] = 1 if vals["featured"] in (1, True, "1", "on", "true") else 0
        if not vals["title"] or not vals["start_date"]:
            raise ValueError("title and start_date are required")
        ts = now()
        if event_id:
            sets = ", ".join(f"{f}=?" for f in fields)
            self.db.execute(f"update events set {sets}, updated_at=? where id=?", [*vals.values(), ts, event_id])
            return event_id
        uid = f"evt-{secrets.token_hex(8)}@bulletin.fpccorinth.org"
        cur = self.db.execute(
            f"insert into events (uid, {', '.join(fields)}, created_at, updated_at) values (?, {', '.join('?' for _ in fields)}, ?, ?)",
            [uid, *vals.values(), ts, ts],
        )
        return int(cur.lastrowid)

    def delete_event(self, event_id: int) -> None:
        self.db.execute("update events set deleted_at=?, updated_at=? where id=?", (now(), now(), event_id))

    # ── standing text ────────────────────────────────────────────────
    def standing(self, key: str) -> Any:
        row = self.db.execute("select json from standing where key=?", (key,)).fetchone()
        if row:
            return json.loads(row["json"])
        path = config.STANDING_DIR / f"{key}.json"
        if path.exists():
            return json.loads(path.read_text())
        return None

    def set_standing(self, key: str, value: Any) -> None:
        self.db.execute(
            "insert into standing (key, json, updated_at) values (?, ?, ?) "
            "on conflict(key) do update set json=excluded.json, updated_at=excluded.updated_at",
            (key, json.dumps(value, ensure_ascii=False, indent=2), now()),
        )

    def standing_keys(self) -> list[str]:
        keys = {p.stem for p in config.STANDING_DIR.glob("*.json")}
        keys |= {r["key"] for r in self.db.execute("select key from standing")}
        return sorted(keys)

    # ── users and magic links ────────────────────────────────────────
    def users(self) -> list[dict]:
        return [dict(r) for r in self.db.execute("select * from users where deleted_at is null order by email")]

    def user_by_email(self, email: str) -> dict | None:
        row = self.db.execute("select * from users where email=? and deleted_at is null", (email.lower().strip(),)).fetchone()
        return dict(row) if row else None

    def user_by_id(self, user_id: int) -> dict | None:
        row = self.db.execute("select * from users where id=? and deleted_at is null", (user_id,)).fetchone()
        return dict(row) if row else None

    def add_user(self, email: str, name: str | None, role: str) -> int:
        if role not in USER_ROLES:
            raise ValueError("bad role")
        email = email.lower().strip()
        existing = self.db.execute("select id from users where email=?", (email,)).fetchone()
        if existing:
            self.db.execute("update users set name=?, role=?, deleted_at=null where id=?", (name, role, existing["id"]))
            return int(existing["id"])
        cur = self.db.execute("insert into users (email, name, role, created_at) values (?, ?, ?, ?)", (email, name, role, now()))
        return int(cur.lastrowid)

    def set_user_role(self, user_id: int, role: str) -> None:
        if role not in USER_ROLES:
            raise ValueError("bad role")
        self.db.execute("update users set role=? where id=?", (role, user_id))

    def remove_user(self, user_id: int) -> None:
        self.db.execute("update users set deleted_at=? where id=?", (now(), user_id))

    def create_magic_link(self, user_id: int, ttl_minutes: int = 20) -> str:
        token = secrets.token_urlsafe(32)
        exp = (datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)).replace(microsecond=0).isoformat()
        self.db.execute("insert into magic_links (token, user_id, created_at, expires_at) values (?, ?, ?, ?)", (token, user_id, now(), exp))
        return token

    def consume_magic_link(self, token: str) -> dict | None:
        row = self.db.execute("select * from magic_links where token=?", (token,)).fetchone()
        if not row or row["used_at"] or row["expires_at"] < now():
            return None
        self.db.execute("update magic_links set used_at=? where token=?", (now(), token))
        self.db.execute("update users set last_login_at=? where id=?", (now(), row["user_id"]))
        return self.user_by_id(row["user_id"])

    def recent_login_attempts(self, email: str, minutes: int = 15) -> int:
        since = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
        row = self.db.execute(
            "select count(*) as n from audit where action='login.request' and user_email=? and ts>=?", (email.lower().strip(), since)
        ).fetchone()
        return int(row["n"])

    # ── audit ────────────────────────────────────────────────────────
    def log(self, action: str, user_email: str | None = None, detail: str | None = None) -> None:
        self.db.execute("insert into audit (ts, user_email, action, detail) values (?, ?, ?, ?)", (now(), user_email, action, detail))

    def last_audit(self, *actions: str) -> dict | None:
        """The most recent audit row with one of these actions."""
        marks = ", ".join("?" for _ in actions)
        row = self.db.execute(f"select * from audit where action in ({marks}) order by id desc limit 1", actions).fetchone()
        return dict(row) if row else None

    def audit(self, limit: int = 200) -> list[dict]:
        return [dict(r) for r in self.db.execute("select * from audit order by id desc limit ?", (limit,))]

    # ── weeks ────────────────────────────────────────────────────────
    def week_status(self, date: str) -> dict:
        row = self.db.execute("select * from weeks where date=?", (date,)).fetchone()
        return dict(row) if row else {"date": date, "status": "draft"}

    def set_week_status(self, date: str, status: str, **extra: str) -> None:
        cols = {"status": status, "updated_at": now(), **extra}
        names = ", ".join(cols)
        marks = ", ".join("?" for _ in cols)
        sets = ", ".join(f"{k}=excluded.{k}" for k in cols)
        self.db.execute(f"insert into weeks (date, {names}) values (?, {marks}) on conflict(date) do update set {sets}", [date, *cols.values()])

    def weeks(self) -> list[dict]:
        return [dict(r) for r in self.db.execute("select * from weeks order by date desc")]

    # ── chat ─────────────────────────────────────────────────────────
    def add_chat(self, date: str, role: str, text: str, user_email: str | None, cost: float | None = None) -> int:
        cur = self.db.execute("insert into chat (date, ts, role, user_email, text, cost_usd) values (?, ?, ?, ?, ?, ?)",
                              (date, now(), role, user_email, text, cost))
        return int(cur.lastrowid)

    def chat_history(self, date: str, limit: int = 60) -> list[dict]:
        rows = self.db.execute("select * from chat where date=? order by id desc limit ?", (date, limit)).fetchall()
        return [dict(r) for r in reversed(rows)]

    def chat_count(self, date: str) -> int:
        return int(self.db.execute("select count(*) as n from chat where date=? and role='user'", (date,)).fetchone()["n"])

    # ── ledger ───────────────────────────────────────────────────────
    def month_spend(self, year_month: str | None = None) -> float:
        ym = year_month or datetime.now(timezone.utc).strftime("%Y-%m")
        row = self.db.execute("select coalesce(sum(cost_usd),0) as c from ledger where substr(ts,1,7)=?", (ym,)).fetchone()
        return float(row["c"])

    def add_ledger(self, job: str, kind: str, model: str, usage: dict, cost_usd: float) -> int:
        """One row per API response. ``usage`` holds the four token counts."""
        cur = self.db.execute(
            "insert into ledger (ts, job, kind, model, input_tokens, cache_write_tokens, cache_read_tokens, output_tokens, cost_usd)"
            " values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (now(), job, kind, model, int(usage.get("input_tokens") or 0), int(usage.get("cache_write_tokens") or 0),
             int(usage.get("cache_read_tokens") or 0), int(usage.get("output_tokens") or 0), float(cost_usd)))
        return int(cur.lastrowid)

    def job_spend(self, job: str) -> float:
        row = self.db.execute("select coalesce(sum(cost_usd),0) as c from ledger where job=?", (job,)).fetchone()
        return float(row["c"])

    def job_calls(self, job: str) -> int:
        return int(self.db.execute("select count(*) as n from ledger where job=?", (job,)).fetchone()["n"])

    def ledger(self, limit: int = 200) -> list[dict]:
        return [dict(r) for r in self.db.execute("select * from ledger order by id desc limit ?", (limit,))]

    def month_ledger_by_kind(self, year_month: str | None = None) -> list[dict]:
        ym = year_month or datetime.now(timezone.utc).strftime("%Y-%m")
        return [dict(r) for r in self.db.execute(
            "select kind, count(*) as calls, sum(input_tokens) as input_tokens, sum(cache_write_tokens) as cache_write_tokens,"
            " sum(cache_read_tokens) as cache_read_tokens, sum(output_tokens) as output_tokens, sum(cost_usd) as cost_usd"
            " from ledger where substr(ts,1,7)=? group by kind order by cost_usd desc", (ym,))]

    def builds_this_week(self, date: str) -> int:
        """Full builds that ran the fit loop for this Sunday (ledger kind 'fit' jobs)."""
        return int(self.db.execute("select count(distinct job) as n from ledger where kind='fit' and job like ?", (f"fit:{date}:%",)).fetchone()["n"])

    # ── site stats (page views of the public site; see pipeline/stats.py) ──
    def visitor_salt(self, day: str) -> str:
        """The random salt for one day's visitor hashes. Older salts are deleted, so old hashes cannot be redone."""
        self.db.execute("insert or ignore into stats_salt (day, salt) values (?, ?)", (day, secrets.token_hex(16)))
        self.db.execute("delete from stats_salt where day<>?", (day,))
        return self.db.execute("select salt from stats_salt where day=?", (day,)).fetchone()["salt"]

    def add_hit(self, ts: str, day: str, hour: int, path: str, visitor: str, referrer: str | None, device: str | None) -> None:
        self.db.execute("insert into hits (ts, day, hour, path, visitor, referrer, device) values (?, ?, ?, ?, ?, ?, ?)",
                        (ts, day, hour, path, visitor, referrer, device))

    def hit_counts(self, day: str, visitor: str) -> tuple[int, int]:
        """(every hit that day, that visitor's hits that day): what the flood limits look at."""
        row = self.db.execute("select count(*) as n, coalesce(sum(visitor=?),0) as mine from hits where day=?", (visitor, day)).fetchone()
        return int(row["n"]), int(row["mine"])

    def hits_total(self, since: str, until: str) -> dict:
        row = self.db.execute("select count(*) as views, count(distinct visitor) as visitors from hits where day between ? and ?",
                              (since, until)).fetchone()
        return dict(row)

    def hits_daily(self, since: str, until: str) -> dict[str, dict]:
        return {r["day"]: dict(r) for r in self.db.execute(
            "select day, count(*) as views, count(distinct visitor) as visitors from hits where day between ? and ? group by day",
            (since, until))}

    def hits_grouped(self, column: str, since: str, until: str, limit: int = 12) -> list[dict]:
        if column not in ("path", "referrer", "device"):
            raise ValueError("bad column")
        return [dict(r) for r in self.db.execute(
            f"select {column} as label, count(*) as views, count(distinct visitor) as visitors from hits"
            f" where day between ? and ? and {column} is not null group by {column} order by views desc, label limit ?",
            (since, until, limit))]

    def hits_by_bulletin(self, limit: int = 12, service_hours: tuple[int, int] = (10, 11)) -> list[dict]:
        """Per Sunday bulletin page: all-time views, and how much of that was on its own Sunday and during the service."""
        return [dict(r) for r in self.db.execute(
            "select substr(path, 9) as date, count(*) as views, count(distinct visitor) as visitors,"
            " coalesce(sum(day = substr(path, 9)), 0) as sunday_views,"
            " count(distinct case when day = substr(path, 9) then visitor end) as sunday_visitors,"
            " count(distinct case when day = substr(path, 9) and hour between ? and ? then visitor end) as service_visitors"
            " from hits where path glob '/scroll/[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'"
            " group by path order by path desc limit ?", (*service_hours, limit))]

    def first_hit_day(self) -> str | None:
        return self.db.execute("select min(day) as d from hits").fetchone()["d"]

    # ── seeding ──────────────────────────────────────────────────────
    def seed_if_empty(self, seed_dir: Path | None = None) -> dict:
        seed_dir = seed_dir or config.SEED_DIR
        done = {"rosters": 0, "events": 0}
        if self.db.execute("select count(*) as n from rosters").fetchone()["n"] == 0:
            path = seed_dir / "rosters.csv"
            if path.exists():
                with path.open(newline="") as fh:
                    for row in csv.DictReader(fh):
                        self.upsert_roster(row["sunday"], row)
                        done["rosters"] += 1
        if self.db.execute("select count(*) as n from events").fetchone()["n"] == 0:
            done["events"] += self._seed_recurring()
            done["events"] += self._seed_events(seed_dir / "events.csv")
        return done

    def _seed_events(self, path: Path) -> int:
        if not path.exists():
            return 0
        from datetime import date as _date

        n = 0
        recurring = [(e["title"].lower(), e["start_date"], _rule_days(e.get("recurrence"))) for e in self.events() if e.get("recurrence")]
        with path.open(newline="") as fh:
            for row in csv.DictReader(fh):
                text = row["text"]
                day = _date.fromisoformat(row["date"])
                listed = [d for d in (row.get("listed_in_bulletins") or "").split() if d]
                # an event the office listed more than a week ahead is one to keep listing until it passes
                featured = any((day - _date.fromisoformat(d)).days > 7 for d in listed)
                # split "A · B · C" lines into separate events on the same date
                for part in [p.strip() for p in text.split("·") if p.strip()]:
                    title, time_text = split_time(part)
                    if any(title.lower() == t and row["date"] >= start and day.weekday() in days for t, start, days in recurring):
                        continue  # already covered by a weekly event
                    self.upsert_event({"title": title, "start_date": row["date"], "time_text": time_text, "featured": featured,
                                       "notes": f"seeded from bulletins {' '.join(listed)}".strip()})
                    n += 1
        return n

    def _seed_recurring(self) -> int:
        weekly = [
            ("Messy Bible Study", "TU", "8 a.m.", "Youth House"),
            ("Kids Quest", "WE", None, None),
            ("Youth House", "WE", None, None),
            ("Messy Bible Study", "WE", "6 p.m.", None),
        ]
        for title, day, time_text, location in weekly:
            self.upsert_event({"title": title, "start_date": "2026-10-20", "time_text": time_text, "location": location,
                               "recurrence": f"WEEKLY;BYDAY={day}", "category": "weekly"})
        return len(weekly)


def _rule_days(recurrence: str | None) -> list[int]:
    codes = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}
    if not recurrence or "BYDAY=" not in recurrence:
        return []
    return [codes[d] for d in recurrence.split("BYDAY=", 1)[1].split(";")[0].split(",") if d in codes]


def split_time(text: str) -> tuple[str, str | None]:
    """'Messy Bible Study, 8 a.m.' -> ('Messy Bible Study', '8 a.m.'). Leaves other text alone."""
    import re
    m = re.match(r"^(.*?),\s*((?:\d{1,2}(?::\d{2})?(?:\s*[–-]\s*\d{1,2}(?::\d{2})?)?\s*(?:a\.m\.|p\.m\.|am|pm))|noon)\s*$", text, re.I)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return text, None
