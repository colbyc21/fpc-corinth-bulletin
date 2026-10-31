"""Constant Contact v3: device-flow OAuth and the handful of calls the Friday email needs.

Tokens live in ``~/.config/bulletin-builder/constant-contact.json`` (mode 600),
next to the env file that holds ``CONSTANT_CONTACT_CLIENT_ID``. The device
flow never uses a client secret. Refresh tokens rotate on every use and
expire after about 180 days unused, so the weekly send keeps them alive.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from builder import config

AUTHZ = "https://authz.constantcontact.com/oauth2/default/v1"
USER_AGENT = "fpc-bulletin-builder/1.0 (+https://bulletin.fpccorinth.org)"  # Okta returns 403 to Python's default agent
API = "https://api.cc.email/v3"
SCOPES = "campaign_data contact_data offline_access"
TOKEN_FILE = Path(os.environ.get("CONSTANT_CONTACT_TOKEN_FILE") or config.ENV_FILE.parent / "constant-contact.json")


class CCError(RuntimeError):
    pass


def client_id() -> str:
    cid = os.environ.get("CONSTANT_CONTACT_CLIENT_ID", "").strip()
    if not cid:
        raise CCError("CONSTANT_CONTACT_CLIENT_ID is not set (it belongs in the env file)")
    return cid


def _post_form(url: str, data: dict) -> dict:
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body, headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded", "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode())
        except Exception:
            raise CCError(f"{url}: HTTP {e.code}") from None


# ── tokens ───────────────────────────────────────────────────────────
def load_tokens() -> dict | None:
    if TOKEN_FILE.exists():
        return json.loads(TOKEN_FILE.read_text())
    return None


def save_tokens(tok: dict) -> None:
    tok = {**tok, "obtained_at": int(time.time())}
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(json.dumps(tok, indent=2) + "\n")
    TOKEN_FILE.chmod(0o600)


def start_device_flow() -> dict:
    """Returns the verification URL and user code to show the person."""
    res = _post_form(f"{AUTHZ}/device/authorize", {"client_id": client_id(), "scope": SCOPES})
    if "device_code" not in res:
        raise CCError(f"device authorize failed: {res}")
    return res


def finish_device_flow(dev: dict, *, wait: bool = True, log=print) -> dict:
    """Poll until the person has entered the code. Saves and returns the tokens."""
    interval = int(dev.get("interval", 5))
    deadline = time.time() + int(dev.get("expires_in", 600))
    while time.time() < deadline:
        res = _post_form(f"{AUTHZ}/token", {"client_id": client_id(), "device_code": dev["device_code"],
                                            "grant_type": "urn:ietf:params:oauth:grant-type:device_code"})
        if "access_token" in res:
            save_tokens(res)
            return res
        err = res.get("error", "")
        if err == "authorization_pending":
            if not wait:
                raise CCError("authorization_pending")
            time.sleep(interval)
            continue
        if err == "slow_down":
            interval += 5
            time.sleep(interval)
            continue
        raise CCError(f"device flow failed: {res}")
    raise CCError("the device code expired before the code was entered; run auth again")


def refresh() -> dict:
    tok = load_tokens()
    if not tok or not tok.get("refresh_token"):
        raise CCError("not authorized yet: run `python -m builder cc auth`")
    res = _post_form(f"{AUTHZ}/token", {"client_id": client_id(), "refresh_token": tok["refresh_token"], "grant_type": "refresh_token"})
    if "access_token" not in res:
        raise CCError(f"token refresh failed: {res}")
    save_tokens(res)
    return res


def access_token() -> str:
    tok = load_tokens()
    if not tok:
        raise CCError("not authorized yet: run `python -m builder cc auth`")
    age = time.time() - tok.get("obtained_at", 0)
    if age > int(tok.get("expires_in", 28800)) - 300:
        tok = refresh()
    return tok["access_token"]


# ── API ──────────────────────────────────────────────────────────────
def call(method: str, path: str, body: dict | None = None, *, retry_auth: bool = True) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{API}{path}", data=data, method=method,
                                 headers={"Authorization": f"Bearer {access_token()}", "Accept": "application/json",
                                          "Content-Type": "application/json", "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read().decode()
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as e:
        if e.code == 401 and retry_auth:
            refresh()
            return call(method, path, body, retry_auth=False)
        detail = e.read().decode(errors="replace")[:800]
        raise CCError(f"{method} {path}: HTTP {e.code} {detail}") from None


def contact_lists() -> list[dict]:
    return call("GET", "/contact_lists?limit=1000").get("lists", [])


def list_id(name: str) -> str:
    for lst in contact_lists():
        if lst["name"].strip().lower() == name.strip().lower():
            return lst["list_id"]
    raise CCError(f"no contact list named {name!r}; lists: {[l['name'] for l in contact_lists()]}")


def create_campaign(name: str, *, subject: str, html: str, from_name: str, from_email: str, reply_to: str,
                    preheader: str | None = None) -> dict:
    """A custom-code (format_type 5) campaign with one activity. Returns the API's campaign object."""
    activity = {"format_type": 5, "from_name": from_name, "from_email": from_email, "reply_to_email": reply_to,
                "subject": subject, "html_content": html}
    if preheader:
        activity["preheader"] = preheader
    return call("POST", "/emails", {"name": name, "email_campaign_activities": [activity]})


def primary_activity_id(campaign: dict) -> str:
    acts = campaign.get("campaign_activities") or []
    for a in acts:
        if a.get("role") == "primary_email":
            return a["campaign_activity_id"]
    if acts:
        return acts[0]["campaign_activity_id"]
    raise CCError("campaign has no activities")


def set_recipients(activity_id: str, list_ids: list[str]) -> dict:
    act = call("GET", f"/emails/activities/{activity_id}")
    act["contact_list_ids"] = list_ids
    for k in ("campaign_activity_id", "campaign_id", "role", "current_status", "created_at", "updated_at", "last_sent_date", "permalink_url"):
        act.pop(k, None)
    return call("PUT", f"/emails/activities/{activity_id}", act)


def send_test(activity_id: str, emails: list[str], note: str | None = None) -> dict:
    body: dict = {"email_addresses": emails}
    if note:
        body["personal_message"] = note
    return call("POST", f"/emails/activities/{activity_id}/tests", body)


def schedule(activity_id: str, when_iso: str = "0") -> dict:
    """``"0"`` means send now; otherwise an ISO-8601 UTC time."""
    return call("POST", f"/emails/activities/{activity_id}/schedules", {"scheduled_date": when_iso})


def campaign_url(campaign_id: str) -> str:
    return f"https://app.constantcontact.com/pages/campaigns/email-details/details/activity/{campaign_id}"
