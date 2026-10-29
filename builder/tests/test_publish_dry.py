"""Publish mechanics without touching the real repo: a scratch git repo stands in."""
import json
import subprocess
from pathlib import Path

from builder.pipeline import publish


def _run(cmd, cwd):
    return subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True).stdout


def test_scroll_index_insert_and_replace(tmp_path):
    repo = tmp_path
    (repo / "src/_data").mkdir(parents=True)
    idx = repo / "src/_data/scrollBulletins.json"
    idx.write_text(json.dumps([{"date": "2026-10-25", "liturgical_day": "Reformation Sunday", "sermon_title": "Excel in Giving", "url": "/scroll/2026-10-25/"}]))
    week = {"date": "2026-11-01", "liturgical_day": "Twenty-third Sunday after Pentecost", "subtitle_override": None,
            "sermon": {"title": "Spiritual Renewal: When God Begins to Work"}}
    publish.update_scroll_index(week, repo)
    entries = json.loads(idx.read_text())
    assert [e["date"] for e in entries] == ["2026-11-01", "2026-10-25"]
    week["sermon"]["title"] = "Changed"
    publish.update_scroll_index(week, repo)
    entries = json.loads(idx.read_text())
    assert len(entries) == 2 and entries[0]["sermon_title"] == "Changed"


def test_commit_message_add_then_update(tmp_path):
    repo = tmp_path
    _run(["git", "init", "-q"], repo)
    _run(["git", "config", "user.email", "t@example.com"], repo)
    _run(["git", "config", "user.name", "t"], repo)
    msg = publish.commit_message("2026-11-01", repo)
    assert msg.startswith("Add scrollable bulletin and print booklet for November 1, 2026")
    assert publish.TRAILER in msg
    (repo / "src/scroll").mkdir(parents=True)
    (repo / "src/scroll/2026-11-01.html").write_text("x")
    _run(["git", "add", "."], repo)
    _run(["git", "commit", "-q", "-m", "init"], repo)
    assert publish.commit_message("2026-11-01", repo).startswith("Update scrollable bulletin")
    assert publish.commit_message("2026-11-01", repo, correction="assisting elder").startswith("Correct assisting elder for November 1, 2026")
