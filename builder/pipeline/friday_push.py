"""Push the Friday email to Constant Contact as a draft, test-send, schedule."""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone

from builder.pipeline import constant_contact as cc, dates, friday
from builder.pipeline.store import Store

LIST_NAME = os.environ.get("CONSTANT_CONTACT_LIST", "FPC Corinth Members")
FROM_NAME = os.environ.get("FRIDAY_FROM_NAME", "First Presbyterian Church")
FROM_EMAIL = os.environ.get("FRIDAY_FROM_EMAIL", "")  # must be a sender address verified in Constant Contact
REPLY_TO = os.environ.get("FRIDAY_REPLY_TO") or FROM_EMAIL


def campaign_name(doc: dict) -> str:
    return f"The Friday Email · {dates.month_day(doc['friday'])}, {doc['friday'][:4]}"


def push(sunday: str, *, store: Store, test_to: list[str] | None = None, user: str | None = None, list_name: str = LIST_NAME) -> dict:
    """Create (or replace) the draft campaign for this week, attach the list, send a test."""
    if not FROM_EMAIL:
        raise cc.CCError("FRIDAY_FROM_EMAIL is not set in the env file (the verified sender address in Constant Contact)")
    doc = friday.load(sunday)
    html = friday.render(doc)
    friday.html_path(sunday).write_text(html)
    sha = hashlib.sha1(html.encode()).hexdigest()[:12]
    name = campaign_name(doc)
    if doc.get("push") and doc["push"].get("campaign_id"):
        name = f"{name} ({datetime.now(timezone.utc).strftime('%H%M')})"  # a fresh draft; the old one stays for comparison
    camp = cc.create_campaign(name, subject=doc["subject"], html=html, from_name=FROM_NAME, from_email=FROM_EMAIL,
                              reply_to=REPLY_TO, preheader=doc.get("preheader"))
    activity = cc.primary_activity_id(camp)
    cc.set_recipients(activity, [cc.list_id(list_name)])
    sent_to = []
    if test_to:
        cc.send_test(activity, test_to, f"Test of {name} from the bulletin builder")
        sent_to = list(test_to)
    doc["push"] = {"campaign_id": camp["campaign_id"], "activity_id": activity, "name": name,
                   "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(), "test_sent_to": sent_to,
                   "scheduled": None, "html_sha": sha}
    friday.save(doc)
    store.log("friday.push", user, json.dumps({"date": sunday, **doc["push"]}))
    return doc["push"]


def schedule(sunday: str, when_iso: str, *, store: Store, user: str | None = None) -> dict:
    doc = friday.load(sunday)
    if not doc.get("push"):
        raise cc.CCError("push the draft first")
    cc.schedule(doc["push"]["activity_id"], when_iso)
    doc["push"]["scheduled"] = when_iso
    friday.save(doc)
    store.log("friday.schedule", user, json.dumps({"date": sunday, "when": when_iso}))
    return doc["push"]


def stale(doc: dict) -> bool:
    """True when the content changed since the last push."""
    if not doc.get("push"):
        return False
    return hashlib.sha1(friday.render(doc).encode()).hexdigest()[:12] != doc["push"].get("html_sha")
