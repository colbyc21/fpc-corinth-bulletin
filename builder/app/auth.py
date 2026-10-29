"""Magic-link sign-in (the fpc-sermons pattern) and role checks.

Nothing works without a login (plan §7). Accounts are created by an admin;
there is no self-signup. A login request for an unknown address gets the
same reply as a known one. Links expire in 20 minutes and are single use.
Sessions are a signed cookie holding the user id, 30 days, SameSite=Lax.
"""
from __future__ import annotations

import logging
import os

import httpx
from fastapi import HTTPException, Request
from itsdangerous import BadSignature, URLSafeTimedSerializer

from builder import config
from builder.pipeline.store import Store

log = logging.getLogger("builder.auth")

COOKIE = "bb_session"
MAX_AGE = 30 * 24 * 3600
ROLE_RANK = {"editor": 1, "publisher": 2, "admin": 3}


def _serializer() -> URLSafeTimedSerializer:
    secret = os.environ.get("SESSION_SECRET")
    if not secret:
        raise RuntimeError("SESSION_SECRET is not set (see ~/.config/bulletin-builder/env)")
    return URLSafeTimedSerializer(secret, salt="bb-session")


def session_cookie(user_id: int) -> str:
    return _serializer().dumps({"uid": user_id})


def user_from_request(request: Request, store: Store) -> dict | None:
    raw = request.cookies.get(COOKIE)
    if not raw:
        return None
    try:
        data = _serializer().loads(raw, max_age=MAX_AGE)
    except BadSignature:
        return None
    return store.user_by_id(int(data.get("uid", 0)))


def require_role(user: dict | None, role: str) -> dict:
    if user is None:
        raise HTTPException(status_code=401)
    if ROLE_RANK[user["role"]] < ROLE_RANK[role]:
        raise HTTPException(status_code=403, detail=f"This needs the {role} role.")
    return user


def can(user: dict | None, role: str) -> bool:
    return bool(user) and ROLE_RANK[user["role"]] >= ROLE_RANK[role]


def secure_cookies() -> bool:
    return config.PUBLIC_BASE_URL.startswith("https://")


def send_magic_link(email: str, link: str) -> bool:
    """Send through Resend. Without a key (dev box) the link goes to the log only."""
    key = os.environ.get("RESEND_API_KEY")
    sender = os.environ.get("EMAIL_FROM", "FPC Bulletin Builder <no-reply@fpccorinth.org>")
    if not key:
        log.warning("RESEND_API_KEY not set; magic link for %s: %s", email, link)
        return False
    body = {
        "from": sender,
        "to": [email],
        "subject": "Your sign-in link for the FPC bulletin builder",
        "text": f"Click to sign in (valid for 20 minutes):\n\n{link}\n\nIf you did not ask for this, ignore it.",
        "html": f"<p>Click to sign in (valid for 20 minutes):</p><p><a href=\"{link}\">{link}</a></p><p>If you did not ask for this, ignore it.</p>",
    }
    r = httpx.post("https://api.resend.com/emails", json=body, headers={"Authorization": f"Bearer {key}"}, timeout=15)
    if r.status_code >= 300:
        log.error("Resend error %s: %s", r.status_code, r.text[:300])
        return False
    return True
