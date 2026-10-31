"""Cloudflare Access allowlist for bulletin-admin.fpccorinth.org.

Access is the outer gate in front of the app: a visitor must pass a one-time
PIN sent to an address on the allow policy before the app's own login is even
reached. The admin Users screen keeps that allowlist in step with the users
table: adding a user adds the address, removing a user removes it.

Configuration comes from ``~/.config/cloudflare/env`` (mode 600), loaded by
``builder.config``: ``CLOUDFLARE_API_TOKEN`` and ``CLOUDFLARE_ACCOUNT_ID``.
The application and policy ids are fixed in ``builder.config`` and can be
overridden with ``CF_ACCESS_APP_ID`` / ``CF_ACCESS_POLICY_ID``. Without a
token (a dev clone) every call is a no-op that reports "not configured".
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request

from builder import config

log = logging.getLogger("builder.cfaccess")
TIMEOUT = 10


class AccessError(RuntimeError):
    """The Cloudflare API refused or could not be reached."""


def configured() -> bool:
    return bool(os.environ.get("CLOUDFLARE_API_TOKEN") and os.environ.get("CLOUDFLARE_ACCOUNT_ID"))


def _policy_url() -> str:
    return (f"https://api.cloudflare.com/client/v4/accounts/{os.environ['CLOUDFLARE_ACCOUNT_ID']}"
            f"/access/apps/{config.CF_ACCESS_APP_ID}/policies/{config.CF_ACCESS_POLICY_ID}")


def _call(method: str = "GET", body: dict | None = None) -> dict:
    req = urllib.request.Request(
        _policy_url(), method=method, data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {os.environ['CLOUDFLARE_API_TOKEN']}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as e:
        try:
            errors = json.load(e)["errors"]
        except Exception:  # noqa: BLE001
            errors = e.reason
        raise AccessError(f"Cloudflare API {e.code}: {errors}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise AccessError(f"Cloudflare API unreachable: {e}") from None
    if not data.get("success"):
        raise AccessError(f"Cloudflare API error: {data.get('errors')}")
    return data["result"]


def _emails(policy: dict) -> list[str]:
    return [rule["email"]["email"].lower() for rule in policy.get("include", []) if "email" in rule]


def allowlist() -> list[str] | None:
    """Addresses on the allow policy, or None when Access is not configured."""
    if not configured():
        return None
    return _emails(_call())


def _put(policy: dict, emails: list[str]) -> list[str]:
    emails = sorted(set(emails))
    if not emails:
        raise AccessError("refusing to empty the allowlist; it would lock everyone out")
    body = {k: policy[k] for k in ("name", "decision", "precedence", "exclude", "require") if k in policy}
    body["include"] = [{"email": {"email": e}} for e in emails]
    return _emails(_call("PUT", body))


def add(email: str) -> list[str] | None:
    """Add one address. Returns the new allowlist, or None when not configured."""
    if not configured():
        return None
    email = email.lower().strip()
    policy = _call()
    current = _emails(policy)
    return current if email in current else _put(policy, current + [email])


def remove(email: str) -> list[str] | None:
    """Remove one address. Returns the new allowlist, or None when not configured."""
    if not configured():
        return None
    email = email.lower().strip()
    policy = _call()
    current = _emails(policy)
    return current if email not in current else _put(policy, [e for e in current if e != email])


def sync(user_emails: list[str]) -> list[str] | None:
    """Make the allowlist exactly the active users. Returns it, or None when not configured."""
    if not configured():
        return None
    return _put(_call(), [e.lower().strip() for e in user_emails])
