"""FastAPI app for the bulletin builder (Phase 1 surface).

Phase 1 ships the deterministic core behind a login: a dashboard of weeks,
a week page (raw week.json editor, build, proof panels, scroll preview,
publish), and the admin screens for rosters, events, standing text and
users. The volunteer form, chat and job progress arrive in Phase 3.

Run:  uvicorn builder.app.main:app --host 127.0.0.1 --port 8710
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from builder import config
from builder.app import auth
from builder.pipeline import build as buildmod, calendar_feed, chat as chatmod, content as contentmod, dates, deploy_status, extract, fit as fitmod, forms, hymn_index, intake as intakemod, scripture_index, model as modelmod, publish as publishmod
from builder.pipeline import week as weekmod
from builder.pipeline.store import ROLE_LABELS, ROSTER_ROLES, USER_ROLES, Store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("builder.app")

app = FastAPI(title="FPC Bulletin Builder", docs_url=None, redoc_url=None, openapi_url=None)
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
templates.env.filters["long_date"] = dates.long_date
templates.env.filters["month_day"] = dates.month_day
templates.env.filters["recurrence_parts"] = calendar_feed.recurrence_parts
templates.env.filters["describe_recurrence"] = calendar_feed.describe_recurrence
templates.env.globals.update(ROLE_LABELS=ROLE_LABELS, ROSTER_ROLES=ROSTER_ROLES, USER_ROLES=USER_ROLES,
                             netlify_url=config.NETLIFY_DEPLOYS_URL, site_url=config.SITE_URL)

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_store: Store | None = None


def store() -> Store:
    global _store
    if _store is None:
        config.ensure_dirs()
        _store = Store()
        _store.seed_if_empty()
    return _store


def current_user(request: Request) -> dict | None:
    return auth.user_from_request(request, store())


def need(role: str):
    def dep(request: Request, user: dict | None = Depends(current_user)) -> dict:
        if user is None:
            raise HTTPException(status_code=401)
        return auth.require_role(user, role)
    return dep


@app.exception_handler(HTTPException)
async def http_errors(request: Request, exc: HTTPException):
    if exc.status_code == 401:
        return RedirectResponse(f"/login?next={request.url.path}", status_code=303)
    return templates.TemplateResponse(request, "error.html", {"user": current_user(request), "spend": store().month_spend(),
                                                             "spend_status": modelmod.spend_status(store()),
                                                              "status": exc.status_code, "detail": exc.detail}, status_code=exc.status_code)


def page(request: Request, name: str, user: dict | None, **ctx):
    ctx.setdefault("flash", request.query_params.get("m"))
    return templates.TemplateResponse(request, name, {"user": user, "spend": store().month_spend(),
                                                     "spend_status": modelmod.spend_status(store()), **ctx})


def check_date(d: str) -> str:
    if not DATE_RE.match(d):
        raise HTTPException(400, "bad date")
    date.fromisoformat(d)
    return d


# ── health and auth ──────────────────────────────────────────────────
@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request, user: dict | None = Depends(current_user)):
    if user:
        return RedirectResponse("/", status_code=303)
    return page(request, "login.html", None, sent=False)


@app.post("/login", response_class=HTMLResponse)
def login_post(request: Request, email: str = Form(...)):
    s = store()
    email = email.strip().lower()
    s.log("login.request", email, request.client.host if request.client else None)
    if s.recent_login_attempts(email) > 5:
        return page(request, "login.html", None, sent=True, note="Too many attempts. Try again in a little while.")
    u = s.user_by_email(email)
    if u:
        token = s.create_magic_link(u["id"])
        link = f"{config.PUBLIC_BASE_URL}/auth/{token}"
        auth.send_magic_link(email, link)
    return page(request, "login.html", None, sent=True)


@app.get("/auth/{token}")
def login_token(token: str, request: Request):
    s = store()
    u = s.consume_magic_link(token)
    if not u:
        return page(request, "login.html", None, sent=False, note="That link has expired or was already used. Ask for a new one.")
    s.log("login.ok", u["email"])
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(auth.COOKIE, auth.session_cookie(u["id"]), max_age=auth.MAX_AGE, httponly=True, samesite="lax", secure=auth.secure_cookies())
    return resp


@app.post("/logout")
def logout(user: dict | None = Depends(current_user)):
    if user:
        store().log("logout", user["email"])
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(auth.COOKIE)
    return resp


# ── dashboard and weeks ──────────────────────────────────────────────
def list_weeks() -> list[dict]:
    s = store()
    status = {w["date"]: w for w in s.weeks()}
    out = []
    for p in sorted(config.BULLETINS_DIR.glob("*/week.json"), reverse=True):
        d = p.parent.name
        try:
            w = json.loads(p.read_text())
            sermon = w.get("sermon", {}).get("title", "")
            sub = w.get("subtitle_override") or w.get("liturgical_day") or ""
        except Exception:
            sermon, sub = "(unreadable week.json)", ""
        out.append({"date": d, "sermon": sermon, "subtitle": sub, **status.get(d, {"status": "draft"})})
    return out


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, user: dict = Depends(need("editor"))):
    nxt = dates.next_sunday()
    return page(request, "dashboard.html", user, weeks=list_weeks()[:30], next_sunday=nxt.isoformat(),
                sundays=[(nxt + timedelta(weeks=i)).isoformat() for i in range(0, 6)])


@app.post("/weeks/new")
def week_new(date_: str = Form(..., alias="date"), user: dict = Depends(need("editor"))):
    d = check_date(date_)
    s = store()
    path = weekmod.week_path(d)
    if not path.exists():
        week = weekmod.scaffold(d, serve=s.serve_for(d), calendar=calendar_feed.week_calendar(s, d))
        weekmod.save_week(week, path)
        s.set_week_status(d, "draft")
        s.log("week.new", user["email"], d)
    return RedirectResponse(f"/weeks/{d}", status_code=303)


@app.get("/weeks/{d}", response_class=HTMLResponse)
def week_page(d: str, request: Request, user: dict = Depends(need("editor"))):
    check_date(d)
    path = weekmod.week_path(d)
    if not path.exists():
        raise HTTPException(404, f"No week.json for {d}. Create it from the dashboard.")
    raw = path.read_text()
    error = None
    try:
        week = weekmod.load_week(d)
    except Exception as e:  # show the raw file even if it no longer validates
        week, error = None, str(e)
    report = buildmod.last_report(d)
    wdir = config.week_dir(d)
    inputs = {slot: (wdir / pattern.format(d=d)).exists() or (wdir / pattern.format(d=d).replace(".rtf", ".txt")).exists()
              for slot, (pattern, _) in UPLOAD_SLOTS.items()}
    return page(request, "week.html", user, d=d, raw=raw, week=week, error=error, report=report,
                status=store().week_status(d), has_pdf=(wdir / f"bulletin-{d}-legal.pdf").exists(),
                has_scroll=(config.SCROLL_DIR / f"{d}.html").exists(),
                panels=sorted(int(k) for k in (report or {}).get("panels", {})),
                inputs=inputs, calendar_text=forms.calendar_lines_text(week["calendar"]) if week else "",
                inserts=forms.INSERTS, chat=store().chat_history(d), chat_left=chatmod.MAX_MESSAGES_PER_WEEK - store().chat_count(d),
                liturgy_summary=[{"id": it["id"], "title": it["title"], "detail": it.get("piece") or it.get("ref") or it.get("name") or ""}
                                 for it in week["liturgy"]["items"] if it["type"] != "section"] if week else [])


@app.post("/weeks/{d}/form")
async def week_form(d: str, request: Request, user: dict = Depends(need("editor"))):
    check_date(d)
    form = await request.form()
    data: dict = {}
    for k, v in form.multi_items():
        if hasattr(v, "filename"):
            continue
        data.setdefault(k, []).append(v)
    s = store()
    try:
        before = weekmod.load_week(d)
        week = forms.form_to_week(before, data)
        weekmod.save_week(week)
    except (ValueError, KeyError) as e:
        return RedirectResponse(f"/weeks/{d}?m=Not+saved:+{_q(str(e))}#details", status_code=303)
    msg, cost = "Saved", 0.0
    if "tidy" in data and contentmod.content_of(week) != contentmod.content_of(before):
        # the same copyedit the content file gets, so typed text comes out in house style
        try:
            raw = contentmod.tidy(d, contentmod.content_of(week), store=s)
            cost = raw.pop("_job")["cost"]
            week = contentmod.merge_tidy(week, contentmod.postprocess(d, raw))
            weekmod.save_week(week)
            n = len([c for c in week["copyedit"]["changes"] if c["where"].startswith(contentmod._OWN_PREFIXES)])
            msg = f"Saved; {n} edit(s) to house style, listed under step 3" if n else "Saved; nothing to copyedit"
        except (modelmod.ModelError, ValueError) as e:
            msg = f"Saved as typed (copyedit skipped: {e})"
    s.set_week_status(d, "draft")
    s.log("week.form", user["email"], json.dumps({"date": d, "cost": cost, "tidy": "tidy" in data}))
    return RedirectResponse(f"/weeks/{d}?m={_q(msg)}#details", status_code=303)


@app.post("/weeks/{d}/refresh/{what}")
def week_refresh(d: str, what: str, user: dict = Depends(need("editor"))):
    """Re-pull the roster row or the calendar lines from the tables into the week."""
    check_date(d)
    s = store()
    week = weekmod.load_week(d)
    if what == "serve":
        week["serve"] = {k: v for k, v in s.serve_for(d).items() if v}
    elif what == "calendar":
        week["calendar"] = calendar_feed.week_calendar(s, d)
    else:
        raise HTTPException(404)
    weekmod.save_week(week)
    s.log("week.refresh", user["email"], f"{d} {what}")
    return RedirectResponse(f"/weeks/{d}?m={_q('Refreshed ' + what + ' from the table')}#details", status_code=303)


@app.post("/weeks/{d}/chat")
def week_chat(d: str, message: str = Form(...), user: dict = Depends(need("editor"))):
    check_date(d)
    message = message.strip()
    if not message:
        return RedirectResponse(f"/weeks/{d}#chat", status_code=303)
    try:
        res = chatmod.revise(d, message, store=store(), user=user["email"])
    except modelmod.ModelError as e:
        return RedirectResponse(f"/weeks/{d}?m={_q('Chat: ' + str(e))}#chat", status_code=303)
    except Exception as e:
        log.exception("chat failed")
        return RedirectResponse(f"/weeks/{d}?m={_q('Chat failed: ' + str(e))}#chat", status_code=303)
    return RedirectResponse(f"/weeks/{d}?m={_q('Done (' + f'${res['cost']:.2f}' + ')')}#chat", status_code=303)


@app.get("/weeks/{d}/deploy.json")
def week_deploy_status(d: str, user: dict = Depends(need("editor"))):
    check_date(d)
    return JSONResponse(deploy_status.status(d, store().week_status(d)))


@app.post("/weeks/{d}/json")
def week_save_json(d: str, request: Request, raw: str = Form(...), user: dict = Depends(need("editor"))):
    check_date(d)
    try:
        week = weekmod.with_defaults(json.loads(raw))
        weekmod.save_week(week)
    except Exception as e:
        return RedirectResponse(f"/weeks/{d}?m=Not+saved:+{_q(str(e))}", status_code=303)
    s = store()
    s.set_week_status(d, "draft")
    s.log("week.edit", user["email"], d)
    return RedirectResponse(f"/weeks/{d}?m=Saved", status_code=303)


MAX_UPLOAD = 10 * 1024 * 1024
UPLOAD_SLOTS = {  # form field -> (filename pattern, accepted suffixes)
    "liturgy": ("{d} Liturgy.docx", (".docx",)),
    "sermon": ("{d} Sermon Notes.docx", (".docx",)),
    "content": ("bulletin-content-{d}.rtf", (".rtf", ".txt")),
    "announcements": ("Announcements.rtf", (".rtf", ".txt")),
}


async def _save_upload(d: str, slot: str, up: UploadFile | None) -> Path | None:
    """Store one untrusted upload under the week folder with the canonical name."""
    if up is None or not up.filename:
        return None
    pattern, suffixes = UPLOAD_SLOTS[slot]
    suffix = Path(up.filename).suffix.lower()
    if suffix not in suffixes:
        raise ValueError(f"{slot}: expected {' or '.join(suffixes)}, got {suffix or 'no extension'}")
    data = await up.read()
    if len(data) > MAX_UPLOAD:
        raise ValueError(f"{slot}: file larger than 10 MB")
    wdir = config.week_dir(d)
    wdir.mkdir(parents=True, exist_ok=True)
    name = pattern.format(d=d)
    if suffix == ".txt":
        name = name.replace(".rtf", ".txt")
    path = wdir / name
    path.write_bytes(data)
    if suffix == ".docx" and not extract.is_docx(path):
        path.unlink()
        raise ValueError(f"{slot}: not a Word document")
    return path


@app.post("/weeks/{d}/intake")
async def week_intake(d: str, user: dict = Depends(need("editor")), liturgy: UploadFile | None = File(None),
                      sermon: UploadFile | None = File(None), content: UploadFile | None = File(None),
                      announcements: UploadFile | None = File(None)):
    check_date(d)
    try:
        paths = {slot: await _save_upload(d, slot, up) for slot, up in (("liturgy", liturgy), ("sermon", sermon), ("content", content), ("announcements", announcements))}
    except ValueError as e:
        return RedirectResponse(f"/weeks/{d}?m=Not+parsed:+{_q(str(e))}", status_code=303)
    paths = {k: v for k, v in paths.items() if v}
    if not paths:
        return RedirectResponse(f"/weeks/{d}?m=Choose+at+least+one+file", status_code=303)
    if "sermon" in paths and "liturgy" not in paths:
        wk = config.week_dir(d) / f"{d} Liturgy.docx"
        if wk.exists():
            paths["liturgy"] = wk
        else:
            return RedirectResponse(f"/weeks/{d}?m=Upload+the+Liturgy+with+the+Sermon+Notes", status_code=303)
    try:
        res = intakemod.intake(d, store=store(), user=user["email"], **paths)
    except modelmod.ModelError as e:
        return RedirectResponse(f"/weeks/{d}?m=Not+parsed:+{_q(str(e))}", status_code=303)
    except Exception as e:
        log.exception("intake failed")
        return RedirectResponse(f"/weeks/{d}?m=Not+parsed:+{_q(str(e))}", status_code=303)
    msg = f"Parsed {', '.join(paths)} (${res['cost']:.2f}); {len(res['changes'])} edit(s), {len(res['flags'])} flag(s)"
    return RedirectResponse(f"/weeks/{d}?m={_q(msg)}", status_code=303)


@app.post("/weeks/{d}/build")
def week_build(d: str, user: dict = Depends(need("editor"))):
    check_date(d)
    try:
        res = fitmod.build_and_fit(d, store=store())
    except modelmod.ModelError as e:
        return RedirectResponse(f"/weeks/{d}?m=Build+failed:+{_q(str(e))}", status_code=303)
    except Exception as e:
        log.exception("build failed")
        return RedirectResponse(f"/weeks/{d}?m=Build+failed:+{_q(str(e))}", status_code=303)
    report = res["report"]
    store().log("week.build", user["email"], json.dumps({"date": d, "overflow": len(report["overflow"]), "fit": res["fit"]}))
    if not report["overflow"]:
        msg = "Built with no overflow"
        if res["fit"] and res["fit"].get("turns"):
            msg += f" after the fit loop ({res['fit']['turns']} turn(s), ${res['fit']['cost']:.2f})"
    else:
        msg = f"Built, but {len(report['overflow'])} panel(s) still overflow"
        if res["fit"]:
            msg += ": " + (res["fit"].get("skipped") or res["fit"].get("summary") or "")
        elif not config.MODEL_CALLS:
            msg += " (model calls are off, so the fit loop did not run)"
    return RedirectResponse(f"/weeks/{d}?m={_q(msg)}", status_code=303)


@app.get("/spend", response_class=HTMLResponse)
def spend_page(request: Request, user: dict = Depends(need("admin"))):
    s = store()
    return page(request, "spend.html", user, by_kind=s.month_ledger_by_kind(), rows=s.ledger(limit=100))


@app.post("/weeks/{d}/ready")
def week_ready(d: str, user: dict = Depends(need("editor"))):
    check_date(d)
    s = store()
    s.set_week_status(d, "ready")
    s.log("week.ready", user["email"], d)
    return RedirectResponse(f"/weeks/{d}?m=Marked+ready", status_code=303)


@app.post("/weeks/{d}/publish")
def week_publish(d: str, correction: str = Form(""), user: dict = Depends(need("publisher"))):
    check_date(d)
    try:
        res = publishmod.publish(d, store=store(), correction=correction.strip() or None, user=user["email"])
    except Exception as e:
        log.exception("publish failed")
        return RedirectResponse(f"/weeks/{d}?m=Publish+failed:+{_q(str(e))}", status_code=303)
    return RedirectResponse(f"/weeks/{d}?m={_q('Published ' + res['commit'] + ' to ' + res['branch'])}", status_code=303)


@app.get("/weeks/{d}/pdf")
def week_pdf(d: str, user: dict = Depends(need("editor"))):
    check_date(d)
    p = config.week_dir(d) / f"bulletin-{d}-legal.pdf"
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p, media_type="application/pdf", filename=p.name)


@app.get("/weeks/{d}/insert/{name}.pdf")
def week_insert(d: str, name: str, user: dict = Depends(need("editor"))):
    check_date(d)
    if not re.match(r"^[a-z0-9-]+$", name):
        raise HTTPException(400)
    p = config.week_dir(d) / f"{name}-insert.pdf"
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p, media_type="application/pdf", filename=p.name)


@app.get("/weeks/{d}/scroll", response_class=HTMLResponse)
def week_scroll(d: str, user: dict = Depends(need("editor"))):
    check_date(d)
    p = config.SCROLL_DIR / f"{d}.html"
    if not p.exists():
        raise HTTPException(404)
    html = p.read_text().replace('src="/images/', f'src="{config.SITE_URL}/images/')
    return HTMLResponse(html)


@app.get("/weeks/{d}/panel/{n}.png")
def week_panel(d: str, n: int, user: dict = Depends(need("editor"))):
    check_date(d)
    p = config.PROOF_DIR / d / f"panel-{n}.png"
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p, media_type="image/png")


# ── rosters ──────────────────────────────────────────────────────────
@app.get("/rosters", response_class=HTMLResponse)
def rosters(request: Request, user: dict = Depends(need("admin"))):
    s = store()
    nxt = dates.next_sunday()
    upcoming = [(nxt + timedelta(weeks=i)).isoformat() for i in range(0, 8)]
    rows = {r["sunday"]: r for r in s.rosters(80)}
    for d in upcoming:
        rows.setdefault(d, {"sunday": d})
    ordered = sorted(rows.values(), key=lambda r: r["sunday"], reverse=True)
    return page(request, "rosters.html", user, rows=ordered, upcoming=set(upcoming))


@app.post("/rosters/{sunday}")
async def roster_save(sunday: str, request: Request, user: dict = Depends(need("admin"))):
    check_date(sunday)
    form = await request.form()
    store().upsert_roster(sunday, {r: form.get(r, "") for r in ROSTER_ROLES})
    store().log("roster.save", user["email"], sunday)
    return RedirectResponse(f"/rosters?m=Saved+{sunday}#r-{sunday}", status_code=303)


# ── events ───────────────────────────────────────────────────────────
@app.get("/events", response_class=HTMLResponse)
def events(request: Request, user: dict = Depends(need("publisher")), all: int = 0):
    s = store()
    today = date.today().isoformat()
    evs = s.events()
    if not all:
        evs = [e for e in evs if e.get("recurrence") or (e.get("end_date") or e["start_date"]) >= today]
    return page(request, "events.html", user, events=evs, show_all=bool(all), today=today,
                preview=calendar_feed.week_calendar(s, dates.next_sunday().isoformat()), next_sunday=dates.next_sunday().isoformat())


async def _event_form(request: Request) -> dict:
    form = await request.form()
    data = {k: (form.get(k) or "").strip() for k in ("title", "start_date", "end_date", "time_text", "location", "notes", "category", "until_date")}
    data["recurrence"] = calendar_feed.make_recurrence(form.get("repeat", ""), form.get("nth", ""), form.get("byday", ""))
    data["featured"] = form.get("featured") == "on"
    return data


@app.post("/events")
async def event_create(request: Request, user: dict = Depends(need("publisher"))):
    try:
        data = await _event_form(request)
        eid = store().upsert_event(data)
    except ValueError as e:
        return RedirectResponse(f"/events?m={_q(str(e))}", status_code=303)
    store().log("event.add", user["email"], json.dumps(data))
    return RedirectResponse(f"/events?m=Added#e-{eid}", status_code=303)


@app.post("/events/{eid}")
async def event_update(eid: int, request: Request, user: dict = Depends(need("publisher"))):
    try:
        data = await _event_form(request)
        store().upsert_event(data, eid)
    except ValueError as e:
        return RedirectResponse(f"/events?m={_q(str(e))}", status_code=303)
    store().log("event.edit", user["email"], json.dumps({"id": eid, **data}))
    return RedirectResponse(f"/events?m=Saved#e-{eid}", status_code=303)


@app.post("/events/{eid}/delete")
def event_delete(eid: int, user: dict = Depends(need("publisher"))):
    store().delete_event(eid)
    store().log("event.delete", user["email"], str(eid))
    return RedirectResponse("/events?m=Removed", status_code=303)


@app.get("/hymns", response_class=HTMLResponse)
def hymns(request: Request, user: dict = Depends(need("editor"))):
    return page(request, "hymns.html", user, idx=hymn_index.build())


@app.get("/scripture", response_class=HTMLResponse)
def scripture(request: Request, user: dict = Depends(need("editor"))):
    return page(request, "scripture.html", user, idx=scripture_index.build())


@app.get("/calendar.ics")
def calendar_ics(user: dict = Depends(need("editor"))):
    return Response(calendar_feed.build_ics(store()), media_type="text/calendar")


# ── standing text ────────────────────────────────────────────────────
@app.get("/standing", response_class=HTMLResponse)
def standing(request: Request, user: dict = Depends(need("admin"))):
    return page(request, "standing.html", user, keys=store().standing_keys(), key=None, raw=None)


@app.get("/standing/{key}", response_class=HTMLResponse)
def standing_edit(key: str, request: Request, user: dict = Depends(need("admin"))):
    if not re.match(r"^[a-z0-9-]+$", key):
        raise HTTPException(400)
    value = store().standing(key)
    if value is None:
        raise HTTPException(404)
    return page(request, "standing.html", user, keys=store().standing_keys(), key=key, raw=json.dumps(value, ensure_ascii=False, indent=2))


@app.post("/standing/{key}")
def standing_save(key: str, raw: str = Form(...), user: dict = Depends(need("admin"))):
    if not re.match(r"^[a-z0-9-]+$", key):
        raise HTTPException(400)
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as e:
        return RedirectResponse(f"/standing/{key}?m={_q('Not saved: ' + str(e))}", status_code=303)
    store().set_standing(key, value)
    store().log("standing.save", user["email"], key)
    return RedirectResponse(f"/standing/{key}?m=Saved", status_code=303)


# ── users and audit ──────────────────────────────────────────────────
@app.get("/users", response_class=HTMLResponse)
def users(request: Request, user: dict = Depends(need("admin"))):
    return page(request, "users.html", user, users=store().users())


@app.post("/users")
def user_add(email: str = Form(...), name: str = Form(""), role: str = Form(...), user: dict = Depends(need("admin"))):
    if role not in USER_ROLES or "@" not in email:
        raise HTTPException(400, "bad email or role")
    store().add_user(email, name.strip() or None, role)
    store().log("user.add", user["email"], f"{email} {role}")
    return RedirectResponse("/users?m=Added", status_code=303)


@app.post("/users/{uid}/role")
def user_role(uid: int, role: str = Form(...), user: dict = Depends(need("admin"))):
    if uid == user["id"] and role != "admin":
        raise HTTPException(400, "You cannot remove your own admin role.")
    store().set_user_role(uid, role)
    store().log("user.role", user["email"], f"{uid} {role}")
    return RedirectResponse("/users?m=Updated", status_code=303)


@app.post("/users/{uid}/delete")
def user_delete(uid: int, user: dict = Depends(need("admin"))):
    if uid == user["id"]:
        raise HTTPException(400, "You cannot remove yourself.")
    store().remove_user(uid)
    store().log("user.remove", user["email"], str(uid))
    return RedirectResponse("/users?m=Removed", status_code=303)


@app.get("/audit", response_class=HTMLResponse)
def audit(request: Request, user: dict = Depends(need("admin"))):
    return page(request, "audit.html", user, rows=store().audit(300))


def _q(s: str) -> str:
    from urllib.parse import quote_plus
    return quote_plus(s[:300])


# ── the Friday email ─────────────────────────────────────────────────
from builder.pipeline import friday as fridaymod, friday_chat, friday_content, friday_forms, friday_push, media as mediamod  # noqa: E402

NOTES_SUFFIXES = (".rtf", ".txt", ".zip")


def _email_ctx(d: str) -> dict:
    exists = fridaymod.path_for(d).exists()
    doc, error = None, None
    if exists:
        try:
            doc = fridaymod.load(d)
        except Exception as e:
            error = str(e)
    s = store()
    return dict(d=d, exists=exists, doc=doc, error=error, friday=fridaymod.friday_of(d),
                has_week=weekmod.week_path(d).exists(), has_html=fridaymod.html_path(d).exists(),
                serve_text=friday_forms.serve_text(doc["serve"]) if doc else "",
                calendar_text=forms.calendar_lines_text(doc["calendar"]) if doc else "",
                birthdays_text=friday_forms.dated_text(doc["birthdays"]) if doc else "",
                anniversaries_text=friday_forms.dated_text(doc["anniversaries"]) if doc else "",
                stale=friday_push.stale(doc) if doc else False,
                chat=s.chat_history(friday_chat.chat_key(d)), chat_left=friday_chat.MAX_MESSAGES - s.chat_count(friday_chat.chat_key(d)),
                campaign_url=cc_url(doc), test_default=",".join(_test_recipients(s)))


def cc_url(doc: dict | None) -> str | None:
    if not doc or not doc.get("push"):
        return None
    from builder.pipeline import constant_contact
    return constant_contact.campaign_url(doc["push"]["campaign_id"])


def _test_recipients(s: Store) -> list[str]:
    return [u["email"] for u in s.users() if u["role"] in ("publisher", "admin")][:5]


def _eredirect(d: str, msg: str | None = None, anchor: str = "content") -> RedirectResponse:
    url = f"/weeks/{d}/email" + (f"?m={_q(msg)}" if msg else "") + f"#{anchor}"
    return RedirectResponse(url, status_code=303)


@app.get("/weeks/{d}/email", response_class=HTMLResponse)
def email_page(d: str, request: Request, user: dict = Depends(need("editor"))):
    check_date(d)
    return page(request, "email.html", user, **_email_ctx(d))


def _fresh_doc(d: str) -> dict:
    s = store()
    week = weekmod.load_week(d) if weekmod.week_path(d).exists() else None
    doc = fridaymod.scaffold(d, week=week, previous=friday_content._previous(d))
    doc["calendar"] = calendar_feed.week_calendar(s, d, horizon_days=14)
    if not doc["serve"]:
        doc["serve"] = fridaymod.serve_lines_from_week({"serve": s.serve_for(d)})
    return doc


@app.post("/weeks/{d}/email/new")
def email_new(d: str, user: dict = Depends(need("editor"))):
    check_date(d)
    if not fridaymod.path_for(d).exists():
        fridaymod.save(_fresh_doc(d))
        store().log("friday.new", user["email"], d)
    return _eredirect(d)


@app.post("/weeks/{d}/email/intake")
async def email_intake(d: str, user: dict = Depends(need("editor")), notes: UploadFile | None = File(None)):
    check_date(d)
    if notes is None or not notes.filename:
        return _eredirect(d, "Choose the office’s notes file", "notes")
    suffix = Path(notes.filename).suffix.lower()
    if suffix not in NOTES_SUFFIXES:
        return _eredirect(d, f"Not parsed: expected .rtf, .txt or a zipped .rtfd, got {suffix or 'no extension'}", "notes")
    data = await notes.read()
    if len(data) > MAX_UPLOAD * 4:
        return _eredirect(d, "Not parsed: file larger than 40 MB", "notes")
    wdir = config.week_dir(d)
    wdir.mkdir(parents=True, exist_ok=True)
    if not fridaymod.path_for(d).exists():
        fridaymod.save(_fresh_doc(d))
    doc = fridaymod.load(d)
    added, skipped = [], []
    if suffix == ".zip":
        try:
            rtf, _, images = mediamod.extract_notes_zip(data)
        except Exception:
            return _eredirect(d, "Not parsed: that zip could not be read", "notes")
        if rtf is None:
            return _eredirect(d, "Not parsed: no .rtf inside the zip", "notes")
        path = wdir / f"friday-notes-{doc['friday']}.rtf"
        path.write_bytes(rtf)
        for name, blob in images:
            try:
                img = mediamod.save_image(d, blob, name)
                if img["src"] not in {m["src"] for m in doc["media"]}:
                    added.append({"src": img["src"], "alt": img["alt"], "width": img["width"]})
            except ValueError:
                skipped.append(name)
        doc["media"] += added
        fridaymod.save(doc)
    else:
        path = wdir / f"friday-notes-{doc['friday']}{suffix}"
        path.write_bytes(data)
    try:
        res = friday_content.intake(d, path, store=store(), user=user["email"])
    except modelmod.ModelError as e:
        return _eredirect(d, f"Not parsed: {e}", "notes")
    except Exception as e:
        log.exception("friday intake failed")
        return _eredirect(d, f"Not parsed: {e}", "notes")
    fl = res["doc"]["copyedit"]["flags"]
    msg = f"Read the notes (${res['cost']:.2f}); {len(res['doc']['copyedit']['changes'])} edit(s), {len(fl)} flag(s)"
    if added:
        msg += f"; {len(added)} photo(s) from the zip are under Photos, ready to attach"
    if skipped:
        msg += f"; could not read {', '.join(skipped)}"
    return _eredirect(d, msg)


async def _email_form_data(request: Request) -> tuple[dict, list[tuple[str, UploadFile]]]:
    form = await request.form()
    data: dict = {}
    files = []
    for k, v in form.multi_items():
        if hasattr(v, "filename"):
            if v.filename:
                files.append((k, v))
        else:
            data[k] = v
    return data, files


async def _apply_email_form(d: str, request: Request) -> tuple[dict, dict, list[str]]:
    """Save what was typed (and any photos chosen) before anything else happens."""
    data, files = await _email_form_data(request)
    before = fridaymod.load(d)
    index_map: dict = {}
    doc = friday_forms.form_to_doc(before, data, index_map)
    notes = []
    for field, up in files:
        m = re.match(r"^s(\d+)_i(\d+)_photo$", field)
        blob = await up.read()
        if len(blob) > MAX_UPLOAD * 2:
            notes.append(f"{up.filename}: larger than 20 MB")
            continue
        try:
            img = mediamod.save_image(d, blob, up.filename or "")
        except ValueError as e:
            notes.append(f"{up.filename}: {e}")
            continue
        image = {"src": img["src"], "alt": img["alt"], "width": img["width"]}
        if image["src"] not in {x["src"] for x in doc["media"]}:
            doc["media"].append(image)
        if m:
            key = (int(m.group(1)), int(m.group(2)))
            if key in index_map:
                doc["sections"][key[0]]["items"][index_map[key]]["image"] = dict(image)
            else:
                notes.append(f"{up.filename}: kept under Photos (the item it was for is empty)")
    return before, doc, notes


@app.post("/weeks/{d}/email/form")
async def email_form(d: str, request: Request, user: dict = Depends(need("editor"))):
    check_date(d)
    s = store()
    try:
        before, doc, notes = await _apply_email_form(d, request)
        fridaymod.save(doc)
    except (ValueError, KeyError) as e:
        return _eredirect(d, f"Not saved: {e}")
    msg, cost = "Saved", 0.0
    data, _ = await _email_form_data(request)
    if "tidy" in data and friday_content._model_view(doc) != friday_content._model_view(before):
        try:
            out = friday_content.tidy(d, doc, store=s)
            cost = out.pop("_job")["cost"]
            fridaymod.save(out)
            doc = out
            n = len(doc["copyedit"]["changes"])
            msg = f"Saved; {n} edit(s) to house style, listed below the form" if n else "Saved; nothing to copyedit"
        except (modelmod.ModelError, ValueError) as e:
            msg = f"Saved as typed (copyedit skipped: {e})"
    if notes:
        msg += "; " + "; ".join(notes)
    try:
        fridaymod.build(d, doc)
    except Exception as e:
        log.exception("friday render failed")
        msg += f"; preview not rendered: {e}"
    s.log("friday.form", user["email"], json.dumps({"date": d, "cost": cost, "tidy": "tidy" in data}))
    return _eredirect(d, msg)


@app.post("/weeks/{d}/email/item")
async def email_item(d: str, request: Request, action: str, si: int, ii: int = 0, user: dict = Depends(need("editor"))):
    """Add, remove or move an item; the typed form is saved first so nothing is lost."""
    check_date(d)
    try:
        _, doc, notes = await _apply_email_form(d, request)
        items = doc["sections"][si]["items"]
        if action == "add":
            items.append(friday_forms.blank_item())
            anchor = f"s{si}_i{len(items) - 1}"
        elif action == "remove":
            items.pop(ii)
            anchor = f"sec{si}"
        elif action in ("up", "down"):
            j = ii - 1 if action == "up" else ii + 1
            if 0 <= j < len(items):
                items[ii], items[j] = items[j], items[ii]
            anchor = f"s{si}_i{j if 0 <= j < len(items) else ii}"
        elif action == "attach":  # a photo from the Photos shelf: ?src=
            src = request.query_params.get("src", "")
            img = next((m for m in doc["media"] if m["src"] == src), None)
            if img is None:
                raise ValueError("that photo is not on the shelf")
            items[ii]["image"] = dict(img)
            anchor = f"s{si}_i{ii}"
        else:
            raise HTTPException(404)
        fridaymod.save(doc)
    except (ValueError, KeyError, IndexError) as e:
        return _eredirect(d, f"Not saved: {e}")
    return _eredirect(d, "; ".join(notes) if notes else None, anchor)


@app.post("/weeks/{d}/email/refresh/{what}")
async def email_refresh(d: str, what: str, request: Request, user: dict = Depends(need("editor"))):
    check_date(d)
    s = store()
    try:
        _, doc, _ = await _apply_email_form(d, request)
    except (ValueError, KeyError) as e:
        return _eredirect(d, f"Not saved: {e}")
    if what == "serve":
        week = weekmod.load_week(d) if weekmod.week_path(d).exists() else {"serve": s.serve_for(d)}
        doc["serve"] = fridaymod.serve_lines_from_week(week)
        src = "the bulletin" if weekmod.week_path(d).exists() else "the roster table"
    elif what == "calendar":
        doc["calendar"] = calendar_feed.week_calendar(s, d, horizon_days=14)
        src = "the calendar table"
    else:
        raise HTTPException(404)
    fridaymod.save(doc)
    s.log("friday.refresh", user["email"], f"{d} {what}")
    return _eredirect(d, f"Refreshed {what} from {src}", "serve" if what == "serve" else "calendar")


@app.get("/weeks/{d}/email/preview.html", response_class=HTMLResponse)
def email_preview(d: str, user: dict = Depends(need("editor"))):
    check_date(d)
    if not fridaymod.path_for(d).exists():
        raise HTTPException(404, "no email yet")
    return HTMLResponse(fridaymod.render(fridaymod.load(d)))


@app.post("/weeks/{d}/email/chat")
def email_chat(d: str, message: str = Form(...), user: dict = Depends(need("editor"))):
    check_date(d)
    message = message.strip()
    if not message:
        return _eredirect(d, None, "chat")
    try:
        res = friday_chat.revise(d, message, store=store(), user=user["email"])
    except modelmod.ModelError as e:
        return _eredirect(d, f"Chat: {e}", "chat")
    except Exception as e:
        log.exception("friday chat failed")
        return _eredirect(d, f"Chat failed: {e}", "chat")
    return _eredirect(d, f"Done (${res['cost']:.2f})", "chat")


@app.post("/weeks/{d}/email/push")
def email_push(d: str, test_to: str = Form(""), user: dict = Depends(need("publisher"))):
    check_date(d)
    emails = [e.strip() for e in re.split(r"[,\s;]+", test_to) if e.strip()]
    bad = [e for e in emails if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", e)]
    if bad:
        return _eredirect(d, f"Not sent: bad address {bad[0]}", "send")
    try:
        res = friday_push.push(d, store=store(), test_to=emails, user=user["email"])
    except Exception as e:
        log.exception("friday push failed")
        return _eredirect(d, f"Push failed: {e}", "send")
    msg = f"Draft “{res['name']}” is in Constant Contact"
    if emails:
        msg += f"; test sent to {', '.join(emails)}"
    return _eredirect(d, msg, "send")


@app.post("/weeks/{d}/email/schedule")
def email_schedule(d: str, when: str = Form(""), user: dict = Depends(need("publisher"))):
    """Schedule the draft to the whole list. ``when`` is a local datetime (America/Chicago); blank = send now."""
    check_date(d)
    from datetime import datetime
    from zoneinfo import ZoneInfo

    if when.strip():
        try:
            local = datetime.fromisoformat(when.strip()).replace(tzinfo=ZoneInfo("America/Chicago"))
        except ValueError:
            return _eredirect(d, "Not scheduled: bad date/time", "send")
        when_iso = local.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")
        label = local.strftime("%A %B %-d at %-I:%M %p")
    else:
        when_iso, label = "0", "now"
    try:
        friday_push.schedule(d, when_iso, store=store(), user=user["email"])
    except Exception as e:
        log.exception("friday schedule failed")
        return _eredirect(d, f"Not scheduled: {e}", "send")
    return _eredirect(d, f"Scheduled to send {label} to the list", "send")


@app.get("/media/{name}")
def media_file(name: str):
    """Photos for the emails: public, so mail clients can fetch them."""
    try:
        path = mediamod.path_of(name)
    except ValueError:
        raise HTTPException(404)
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path, headers={"Cache-Control": "public, max-age=31536000, immutable"})
