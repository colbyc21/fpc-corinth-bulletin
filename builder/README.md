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
| `pipeline/calendar_feed.py` | events table → back-cover lines, `src/calendar.ics`, `/calendar/` page data |
| `pipeline/hymn_index.py` | published scroll pages → hymn index (`/hymns/` page data, app Hymns screen): times sung, last sung, hymnary.org link |
| `pipeline/store.py` | SQLite: rosters, events, standing text, users, magic links, audit, week status, spend ledger |
| `pipeline/names.py` | confirmed name spellings, applied to every build |
| `pipeline/extract.py` | docx / rtf → text, read by the parse step |
| `pipeline/model.py` | the only Claude API client: caps, kill switch, ledger pricing, streaming, structured output |
| `pipeline/parse.py` | model call 1: Liturgy + Sermon Notes docx → liturgy items, margin notes, sermon; hymnal and Pew Bible checks |
| `pipeline/content.py` | model call 2: content form + pastor's announcements → flowers, announcements, serve, calendar, with `changes`/`flags` |
| `pipeline/intake.py` | runs the two calls on the uploaded files and writes week.json |
| `pipeline/fit.py` | model call 3: the fit loop (tools `read_week`, `set_layout`, `render_and_measure`) and `build_and_fit` |
| `pipeline/chat.py` | model call 4: the chat box (edit_week by JSON pointer + the fit tools), 4 calls / $1 per message |
| `pipeline/forms.py` | the volunteer form ↔ week.json |
| `pipeline/deploy_status.py` | is the published week live? Netlify API if `NETLIFY_TOKEN`/`NETLIFY_SITE_ID` are set, else the scroll page's `fpc-build` marker |
| `prompts/*.md` | the four prompts; edit these, not the code, to change model behaviour |
| `app/` | FastAPI app: magic-link login; the week page (Inputs → Details → Build & proof → Publish, with chat); rosters, events, standing text, users, audit, spend |
| `data/seed/`, `data/standing/` | Phase 0 seeds; the store loads them once |
| `tests/` | pytest; `test_render_golden.py` is the Phase 1 acceptance test |
| `deploy/bulletin-builder.service` | user-level systemd unit |

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

## week.json in one paragraph

`liturgy.items` is the ordered service, each item one of `section`, `plain`,
`scripture`, `responsive`, `corporate`, `hymn`, `music`, `speaker`, `reading`,
`sermon`. `liturgy.notes` are the margin notes keyed by item id. `layout.pages`
says which item ids print on p3–p6; `layout.notes` moves a note into a
neighbouring row with `grid-row: span N` (the fix the knowledge note describes);
`layout.page_style` turns off `spread` or sets `row-gap` per page;
`layout.overrides` holds free CSS the fit loop or chat step records. Everything
else (flowers, announcements, serve, calendar, sermon) is plain content.
