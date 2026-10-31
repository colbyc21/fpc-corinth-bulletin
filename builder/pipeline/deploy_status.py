"""Is the published week live yet?

Two ways to know, tried in order:

1. The Netlify API, when ``NETLIFY_TOKEN`` and ``NETLIFY_SITE_ID`` are in the
   env file: the deploy whose ``commit_ref`` is the published commit, with its
   state (building, ready, error) and error message.
2. The live page itself: the scroll page carries ``<meta name="fpc-build">``
   with the build id that was pushed; when the live page shows that id, the
   deploy is done. No credentials needed; this is what runs on basecamp today.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

from builder import config

TIMEOUT = 8
_META = re.compile(r'<meta name="fpc-build" content="([^"]+)"')


def _get(url: str, headers: dict | None = None) -> tuple[int, str]:
    req = urllib.request.Request(url, headers={"User-Agent": "fpc-bulletin-builder", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except (urllib.error.URLError, TimeoutError, OSError):
        return 0, ""


def netlify_state(commit: str) -> dict | None:
    token, site = os.environ.get("NETLIFY_TOKEN"), os.environ.get("NETLIFY_SITE_ID")
    if not (token and site and commit):
        return None
    code, body = _get(f"https://api.netlify.com/api/v1/sites/{site}/deploys?per_page=15", {"Authorization": f"Bearer {token}"})
    if code != 200:
        return {"source": "netlify", "state": "unknown", "detail": f"Netlify API returned {code}"}
    for d in json.loads(body or "[]"):
        if (d.get("commit_ref") or "").startswith(commit):
            state = d.get("state")
            return {"source": "netlify", "state": {"ready": "live", "error": "failed"}.get(state, "building"),
                    "detail": d.get("error_message") or d.get("title") or state, "url": d.get("deploy_ssl_url") or d.get("url"),
                    "admin_url": d.get("admin_url")}
    return {"source": "netlify", "state": "building", "detail": "no deploy for this commit yet"}


def live_state(date: str, build_id: str | None) -> dict:
    url = f"{config.SITE_URL}/scroll/{date}/"
    code, body = _get(url)
    if code == 0:
        return {"source": "live", "state": "unknown", "detail": "could not reach the site", "url": url}
    if code != 200:
        return {"source": "live", "state": "building", "detail": f"page not there yet ({code})", "url": url}
    m = _META.search(body)
    live_id = m.group(1) if m else None
    if build_id and live_id == build_id:
        return {"source": "live", "state": "live", "detail": "the published build is live", "url": url, "build": live_id}
    return {"source": "live", "state": "building", "detail": f"site still shows build {live_id or '(unmarked)'}", "url": url, "build": live_id}


def status(date: str, week_row: dict) -> dict:
    """``week_row`` is the store's weeks row. Returns {state, detail, source, url}."""
    commit = week_row.get("published_commit")
    if not commit:
        return {"state": "unpublished", "detail": "not published yet", "source": "none"}
    n = netlify_state(commit)
    if n and n["state"] != "unknown":
        return {**n, "commit": commit}
    return {**live_state(date, week_row.get("published_build")), "commit": commit}
