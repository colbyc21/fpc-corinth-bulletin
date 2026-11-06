# Bulletin builder

Self-service production of the Sunday bulletin on basecamp. The plan is in
`docs/basecamp-bulletin-builder-plan.md`; what was found on the server is in
`docs/basecamp-discovery.md`. Read `docs/knowledge/*.md` before changing templates.

## Layout

| Path | What |
|---|---|
| `pipeline/week.py` | load / validate / save `bulletins/<date>/week.json` (schema in `pipeline/schema/`) |
| `pipeline/render.py` + `templates/*.j2` | week.json → print booklet HTML and scroll page HTML |
| `pipeline/pdf.py` | Playwright PDF, per-panel overflow measurement, PNG proofs, PDF comparison |
| `pipeline/build.py` | one build: names → render → PDF → measure → proofs → `var/proofs/<date>/build.json` |
| `pipeline/publish.py` | scroll index, calendar files, Eleventy check, commit, push (deploy key) |
| `pipeline/calendar_feed.py` | events table → back-cover lines, `src/calendar.ics`, `/calendar/` page data (the upcoming list, a three-month grid the page shows a month at a time, and `src/calendar-print/`: a one-page landscape PDF of each month, scaled to fit, which is what “Print this month” opens; `templates/calendar-month.html.j2`), the app's month view; one date of a repeating event can be skipped or moved (`event_exceptions`: EXDATE / RECURRENCE-ID in the feed) |
| `app/calendar_sync.py` + `publish.publish_calendar` | the public calendar keeps itself current: a calendar-only commit and push a few minutes after an edit on the Calendar screen, and nightly from `deploy/bulletin-calendar.timer` (see below) |
| `pipeline/hymn_index.py` | published scroll pages → hymn index (`/hymns/` page data, app Hymns screen): times sung, last sung, hymnary.org link |
| `pipeline/store.py` | SQLite: rosters (the Those Who Serve roles, plus who has the flowers, which is not printed), events, standing text, users, magic links, audit, week status, spend ledger |
| `pipeline/names.py` | confirmed name spellings, applied to every build |
| `pipeline/extract.py` | docx / rtf → text, read by the parse step |
| `pipeline/model.py` | the only Claude API client: caps, kill switch, ledger pricing, streaming, structured output |
| `pipeline/parse.py` | model call 1: Liturgy + Sermon Notes docx → liturgy items, margin notes, sermon; hymnal and Pew Bible checks |
| `pipeline/content.py` | model call 2: content form + pastor's announcements → flowers, announcements, serve, calendar, with `changes`/`flags` |
| `pipeline/intake.py` | runs the two calls on the uploaded files and writes week.json |
| `pipeline/fit.py` | model call 3: the fit loop (tools `read_week`, `set_layout`, `render_and_measure`) and `build_and_fit` |
| `pipeline/chat.py` | model call 4: the chat box (edit_week by JSON pointer + the fit tools), 4 calls / $1 per message |
| `pipeline/forms.py` | the volunteer form ↔ week.json |
| `pipeline/friday.py` + `templates/email.html.j2` | the Friday email: `bulletins/<sunday>/friday.json` → 600 px table-layout HTML |
| `pipeline/friday_content.py` | model call: the office's notes → sections, merged with the calendar and roster tables (`prompts/friday.md`) |
| `pipeline/friday_forms.py` / `friday_chat.py` / `friday_push.py` | the email page's form ↔ friday.json; chat corrections (`prompts/friday_chat.md`); Constant Contact draft, test send, schedule |
| `pipeline/media.py` | photos for the email: resized, stored under `var/media`, served publicly at `/media/<name>` |
| `pipeline/constant_contact.py` | Constant Contact v3 client: device-flow OAuth, lists, campaigns, tests, schedule (`python -m builder cc auth`) |
| `pipeline/stats.py` | page views of the public site: the `/hit` beacon's rules (robots, floods, one visitor = one device once a day, nothing personal kept) and the numbers on the app's Stats screen |
| `pipeline/deploy_status.py` | is the published week live? Netlify API if `NETLIFY_TOKEN`/`NETLIFY_SITE_ID` are set, else the scroll page's `fpc-build` marker |
| `prompts/*.md` | the four prompts; edit these, not the code, to change model behaviour |
| `app/` | FastAPI app: magic-link login; the week page (Inputs → Details → Build & proof → Publish, with chat); the Friday email page (Notes → Content with per-item photos → Preview & chat → Send); rosters, events, standing text, users, audit, spend |
| `data/seed/`, `data/standing/` | Phase 0 seeds; the store loads them once |
| `tests/` | pytest; `test_render_golden.py` is the Phase 1 acceptance test |
| `deploy/bulletin-builder.service`, `deploy/bulletin-calendar.{service,timer}` | user-level systemd units: the app, and the nightly calendar refresh |

## Running on basecamp

```
cd ~/bulletin-builder/repo
PY=~/bulletin-builder/venv/bin/python
$PY -m builder init-db
$PY -m builder new 2026-11-08        # scaffold week.json from rosters + calendar
$PY -m builder intake 2026-11-08 --liturgy "2026-11-08 Liturgy.docx" --sermon "2026-11-08 Sermon Notes.docx" \
    --content bulletin-content-2026-11-08.rtf --announcements Announcements.rtf   # model parse → week.json
$PY -m builder build 2026-11-08 --fit  # HTML, PDF, overflow table, proof PNGs; --fit runs the model fit loop on overflow
$PY -m builder spend                 # this month's model spend by kind
$PY -m builder publish 2026-11-08    # commit + push to main (add --branch X to test)
$PY -m pytest builder/tests -q
```

Environment: `BUILDER_REPO` (clone to build in; defaults to the repo this
code is in), `BUILDER_VAR` (`~/bulletin-builder/var`), secrets in
`~/.config/bulletin-builder/env` (`SESSION_SECRET`, `RESEND_API_KEY`,
`EMAIL_FROM`, `ANTHROPIC_API_KEY`, `BUILDER_MODEL_CALLS`). `PUBLIC_BASE_URL` is set in the unit file: it is the address the magic links point at and, when it is https, makes the session cookie Secure.

## Model steps and spend

Three call kinds, all on `claude-opus-5-5` (`BUILDER_MODEL` overrides):
*parse* and *content* use structured output (the API guarantees the JSON
matches the schema in the module), *fit* is a tool loop. Every response's
usage is priced into the `ledger` table; the header shows the month's total
against `BUILDER_MONTHLY_CAP_USD` (default 40). Per-job caps are in
`config.JOB_CAPS`. `BUILDER_MODEL_CALLS=off` turns every model step into a
clear error while rendering and publishing keep working. The Phase 2
acceptance run is `python -m builder.scripts.phase2_acceptance` against a
scratch clone (`BUILDER_REPO`), never the working repo.

## Keeping the public calendar current

The calendar page, the `.ics` feed and the printable months no longer wait
for a bulletin. `publish.publish_calendar` regenerates them from the events
table and commits and pushes those files alone, and only when something in
them changed. It runs:

* **after an edit**: saving, removing, skipping or moving an event on the
  Calendar screen starts a countdown (`BUILDER_CALENDAR_PUSH_DELAY`, 300
  seconds); each further edit restarts it, so a run of edits is one deploy.
  `BUILDER_CALENDAR_AUTOPUBLISH=off` in the env file turns this off.
* **every night** at 12:10 a.m. in Corinth, from the systemd timer
  (`python -m builder calendar --publish`): the turn of a month, and anything
  an app restart or a failed push left behind.
* **on demand**: “Send to the site now” on the Calendar screen.

The files are stable so that this costs nothing when nothing changed: the
feed's DTSTAMP is each event's own last edit, the data file is only rewritten
when its content differs, and the list starts on the first of the month (the
page hides the days that have passed, so it is right on any day without a
rebuild). The publish refuses to run when the clone is off main or holds
unpushed commits, and takes its commit back if the push fails; the Calendar
screen shows the last send and the last failure, both from the audit log
(`calendar.publish`, `calendar.publish.failed`). A file lock in `BUILDER_VAR`
keeps it and the weekly publish from committing at once.

```
cp builder/deploy/bulletin-calendar.{service,timer} ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now bulletin-calendar.timer
systemctl --user start bulletin-calendar.service     # run it now
journalctl --user -u bulletin-calendar -n 50
```

## Site stats

`.eleventy.js` appends `src/_includes/stats-beacon.js`, inline, to every built
page except the forwarding front page and the 404. On
`bulletin.fpccorinth.org` only, it posts the page's path and referrer to
`POST /hit` on this app (public, like `/media`). `pipeline/stats.py` drops
robots, anything not sent from the site, and anyone signed in to the app, and
writes one row to `hits`: the day and hour in Corinth, the path, phone /
tablet / computer, the referring site, and a visitor hash. The hash is of
address + browser under a random salt that is deleted when the day ends, so
no address is stored and a visitor cannot be followed from one day to the
next; no cookie is set. The Stats screen (`/stats`, any signed-in user) shows
views and visitors per day, per bulletin (with how many followed along on the
Sunday between 10 a.m. and noon), per page, per device and per referring
site. The two host names are written into the beacon file; change them there
and in `config.SITE_URL` together.

## week.json in one paragraph

`liturgy.items` is the ordered service, each item one of `section`, `plain`,
`scripture`, `responsive`, `corporate`, `hymn`, `music`, `speaker`, `reading`,
`sermon`. `liturgy.notes` are the margin notes keyed by item id. `layout.pages`
says which item ids print on p3–p6; `layout.notes` moves a note into a
neighbouring row with `grid-row: span N` (the fix the knowledge note describes);
`layout.page_style` turns off `spread` or sets `row-gap` per page;
`layout.overrides` holds free CSS the fit loop or chat step records. Everything
else (flowers, announcements, serve, calendar, sermon) is plain content.
