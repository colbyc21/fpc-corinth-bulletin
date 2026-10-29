"""SQLite store: rosters, events, standing text, users, audit log, week status.

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
USER_ROLES = ("editor", "publisher", "admin")

SCHEMA = """
create table if not exists rosters (
  sunday text primary key,
  assisting_elder text, greeters text, ushers text, acolytes text,
  nursery text, circle_of_prayer text, childrens_church text, av_engineer text,
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
  recurrence text,          -- e.g. WEEKLY;BYDAY=TU  (null = one-off)
  until_date text,          -- last date a recurring event occurs
  featured integer not null default 0,  -- list in every bulletin until it passes
  created_at text not null,
  updated_at text not null,
  deleted_at text
);
create index if not exists events_start on events(start_date);
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
create table if not exists ledger (
  id integer primary key autoincrement,
  ts text not null,
  job text, kind text, model text,
  input_tokens integer, cache_write_tokens integer, cache_read_tokens integer, output_tokens integer,
  cost_usd real not null
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
        cols = ", ".join(ROSTER_ROLES)
        marks = ", ".join("?" for _ in ROSTER_ROLES)
        sets = ", ".join(f"{c}=excluded.{c}" for c in ROSTER_ROLES)
        vals = [(values.get(c) or "").strip() or None for c in ROSTER_ROLES]
        self.db.execute(
            f"insert into rosters (sunday, {cols}, updated_at) values (?, {marks}, ?) "
            f"on conflict(sunday) do update set {sets}, updated_at=excluded.updated_at",
            [sunday, *vals, now()],
        )

    def serve_for(self, sunday: str) -> dict:
        row = self.roster(sunday) or {}
        return {r: row.get(r) for r in ROSTER_ROLES}

    # ── events ───────────────────────────────────────────────────────
    def events(self, include_deleted: bool = False) -> list[dict]:
        sql = "select * from events" + ("" if include_deleted else " where deleted_at is null") + " order by start_date, id"
        return [dict(r) for r in self.db.execute(sql).fetchall()]

    def event(self, event_id: int) -> dict | None:
        row = self.db.execute("select * from events where id=?", (event_id,)).fetchone()
        return dict(row) if row else None

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

    # ── ledger (Phase 2 fills this) ──────────────────────────────────
    def month_spend(self, year_month: str | None = None) -> float:
        ym = year_month or datetime.now(timezone.utc).strftime("%Y-%m")
        row = self.db.execute("select coalesce(sum(cost_usd),0) as c from ledger where substr(ts,1,7)=?", (ym,)).fetchone()
        return float(row["c"])

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
