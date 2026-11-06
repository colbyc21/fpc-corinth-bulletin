"""Publish a built week: index entry, calendar files, Eleventy check, commit, push.

Runs against the repo clone in ``config.REPO`` (on basecamp that is the
deploy clone at ~/bulletin-builder/repo, whose ``core.sshCommand`` already
points at the deploy key). The app never deletes from the repo; a rebuild
overwrites that week's generated files only (and the printable calendar
months, which are replaced as the calendar changes).

``publish_calendar`` is the small sibling: the calendar files alone, pushed
when they change, after an edit on the Calendar screen and every night.
"""
from __future__ import annotations

import fcntl
import json
import os
import subprocess
from contextlib import contextmanager
from pathlib import Path

from builder import config
from builder.pipeline import calendar_feed, dates, hymn_index, scripture_index
from builder.pipeline import week as weekmod
from builder.pipeline.store import Store

TRAILER = "Co-Authored-By: Claude <noreply@anthropic.com>"
CALENDAR_SUBJECT = "Update the church calendar page, feed and printable months"


class PublishError(RuntimeError):
    pass


@contextmanager
def publishing():
    """One publish at a time. The weekly publish, the push that follows a calendar edit and the
    nightly timer all commit in the same clone, from the app and from a separate process."""
    config.ensure_dirs()
    with open(config.VAR / "publish.lock", "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _git(repo: Path, *args: str, check: bool = True) -> str:
    env = dict(os.environ)
    if config.DEPLOY_KEY.exists() and "GIT_SSH_COMMAND" not in env:
        env["GIT_SSH_COMMAND"] = f"ssh -i {config.DEPLOY_KEY} -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
    proc = subprocess.run(["git", *args], cwd=repo, env=env, capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise PublishError(f"git {' '.join(args)} failed:\n{proc.stdout}{proc.stderr}")
    return proc.stdout.strip()


def update_scroll_index(week: dict, repo: Path) -> Path:
    path = repo / "src/_data/scrollBulletins.json"
    entries = json.loads(path.read_text()) if path.exists() else []
    date = week["date"]
    entry = {
        "date": date,
        "liturgical_day": weekmod.subtitle(week),
        "sermon_title": week["sermon"]["title"],
        "url": f"/scroll/{date}/",
    }
    entries = [e for e in entries if e.get("date") != date]
    entries.append(entry)
    entries.sort(key=lambda e: e["date"], reverse=True)
    path.write_text(json.dumps(entries, indent=2, ensure_ascii=False) + "\n")
    return path


def eleventy_check(repo: Path) -> str:
    proc = subprocess.run(["npm", "run", "build", "--silent"], cwd=repo, capture_output=True, text=True)
    # Tailwind rewrites src/css/style.css on every build; never commit that churn.
    subprocess.run(["git", "checkout", "--", "src/css/style.css"], cwd=repo, capture_output=True)
    if proc.returncode != 0:
        raise PublishError(f"Eleventy build failed:\n{proc.stdout}\n{proc.stderr}")
    return proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else "built"


def paths_for(date: str, repo: Path) -> list[str]:
    paths = [
        f"bulletins/{date}",
        f"src/scroll/{date}.html",
        "src/_data/scrollBulletins.json",
        "src/calendar.ics",
        "src/_data/calendarEvents.json",
        "src/_data/hymnIndex.json",
        "qrcode.png",
    ]
    if any((repo / calendar_feed.PRINT_DIR).glob("*.pdf")):  # the printable months; absent until the first is rendered
        paths.append(calendar_feed.PRINT_DIR)
    return paths


def commit_message(date: str, repo: Path, correction: str | None = None) -> str:
    long = dates.long_date(date)
    tracked = subprocess.run(["git", "ls-files", "--error-unmatch", f"src/scroll/{date}.html"], cwd=repo, capture_output=True).returncode == 0
    if correction:
        subject = f"Correct {correction} for {long}"
    elif tracked:
        subject = f"Update scrollable bulletin and print booklet for {long}"
    else:
        subject = f"Add scrollable bulletin and print booklet for {long}"
    return f"{subject}\n\n{TRAILER}\n"


def publish(date: str, *, store: Store | None = None, branch: str | None = None, push: bool = True,
            correction: str | None = None, user: str | None = None, repo: Path | None = None) -> dict:
    """Commit the week and push.

    ``branch=None`` pushes the current branch (main). A named branch pushes
    HEAD to that remote branch instead and then resets the local branch to
    the remote's main so a test publish leaves no stray local commit (the
    working tree, including the built files, is kept).
    """
    with publishing():
        return _publish(date, store=store, branch=branch, push=push, correction=correction, user=user, repo=repo)


def _publish(date: str, *, store: Store | None, branch: str | None, push: bool, correction: str | None, user: str | None,
             repo: Path | None) -> dict:
    repo = repo or config.REPO
    store = store or Store()
    week = weekmod.load_week(date)
    wdir = repo / "bulletins" / date
    if not (wdir / f"bulletin-{date}-legal.pdf").exists():
        raise PublishError(f"{date} has not been built yet")

    update_scroll_index(week, repo)
    calendar_feed.write_site_files(store, repo)
    hymn_index.write_site_data(repo)  # reads the scroll pages, including this week's
    scripture_index.write_site_data(repo)
    build_line = eleventy_check(repo)

    current = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    _git(repo, "add", "-A", "--", *paths_for(date, repo))
    if not _git(repo, "status", "--porcelain", "--", *paths_for(date, repo)):
        raise PublishError("nothing to publish: no changes in the week's files")
    msg = commit_message(date, repo, correction)
    _git(repo, "commit", "-q", "-m", msg)
    sha = _git(repo, "rev-parse", "--short", "HEAD")

    result = {"date": date, "commit": sha, "branch": branch or current, "pushed": False, "eleventy": build_line,
              "message": msg.splitlines()[0]}
    if push:
        target = branch or current
        _git(repo, "push", "-q", "origin", f"HEAD:refs/heads/{target}")
        result["pushed"] = True
        if branch and branch != current:
            # keep local main aligned with origin/main; built files stay in the working tree
            _git(repo, "fetch", "-q", "origin", current)
            _git(repo, "reset", "-q", "--mixed", f"origin/{current}")
        else:
            store.set_week_status(date, "published", published_at=config_now(), published_commit=sha,
                                  published_build=build_id_of(repo / "src/scroll" / f"{date}.html"))
    store.log("publish", user, json.dumps(result))
    return result


def calendar_paths(repo: Path) -> list[str]:
    paths = ["src/calendar.ics", "src/_data/calendarEvents.json"]
    if any((repo / calendar_feed.PRINT_DIR).glob("*.pdf")):
        paths.append(calendar_feed.PRINT_DIR)
    return paths


def publish_calendar(*, store: Store | None = None, repo: Path | None = None, push: bool = True, user: str | None = None) -> dict:
    """Bring the public calendar (the /calendar/ page data, the feed people
    subscribe to, the printable months) up to date with the events table and
    the current month, and push it if that changed anything.

    This is what keeps the public calendar current between bulletins: the app
    calls it a few minutes after an edit on the Calendar screen, and
    ``deploy/bulletin-calendar.timer`` calls it every night. It commits the
    calendar files and nothing else, whatever state a week in progress is in.
    It refuses, rather than push someone else's work, when the clone is not on
    main or holds commits that were never pushed; a push that fails takes its
    commit back, so the next run starts clean."""
    repo = repo or config.REPO
    store = store or Store()
    with publishing():
        current = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
        if current != "main":
            raise PublishError(f"the clone is on {current}, not main")
        if push:
            sync_main(repo)
            if _git(repo, "rev-list", "--count", "origin/main..HEAD") != "0":
                raise PublishError("the clone holds commits that were never pushed; push or drop them first")
        calendar_feed.write_site_files(store, repo)
        paths = calendar_paths(repo)
        _git(repo, "add", "-A", "--", *paths)
        if not _git(repo, "status", "--porcelain", "--", *paths):
            return {"changed": False}
        build_line = eleventy_check(repo)
        _git(repo, "commit", "-q", "-m", CALENDAR_SUBJECT, "--", *paths)
        result = {"changed": True, "commit": _git(repo, "rev-parse", "--short", "HEAD"), "pushed": False, "eleventy": build_line}
        if push:
            try:
                _git(repo, "push", "-q", "origin", "HEAD:refs/heads/main")
            except PublishError:
                _git(repo, "reset", "-q", "--soft", "HEAD~1")  # keep the files, drop the commit: the next run makes it again
                raise
            result["pushed"] = True
        store.log("calendar.publish", user, json.dumps(result))
        return result


def build_id_of(scroll_path: Path) -> str | None:
    """The fpc-build marker in the scroll page that is being pushed."""
    import re

    if not scroll_path.exists():
        return None
    m = re.search(r'<meta name="fpc-build" content="([^"]+)"', scroll_path.read_text())
    return m.group(1) if m else None


def config_now() -> str:
    from builder.pipeline.store import now

    return now()


def sync_main(repo: Path | None = None) -> str:
    """Fast-forward the clone before a build so publishes do not diverge."""
    repo = repo or config.REPO
    _git(repo, "fetch", "-q", "origin", "main")
    try:
        _git(repo, "merge", "-q", "--ff-only", "origin/main")
    except PublishError as e:
        raise PublishError("local main has diverged from origin/main; resolve by hand") from e
    return _git(repo, "rev-parse", "--short", "HEAD")
