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
| `pipeline/store.py` | SQLite: rosters, events, standing text, users, magic links, audit, week status, spend ledger |
| `pipeline/names.py` | confirmed name spellings, applied to every build |
| `pipeline/extract.py` | docx / rtf → text (Phase 2 parse reads this) |
| `app/` | FastAPI app: magic-link login, weeks, rosters, events, standing text, users, audit |
| `data/seed/`, `data/standing/` | Phase 0 seeds; the store loads them once |
| `tests/` | pytest; `test_render_golden.py` is the Phase 1 acceptance test |
| `deploy/bulletin-builder.service` | user-level systemd unit |

## Running on basecamp

```
cd ~/bulletin-builder/repo
PY=~/bulletin-builder/venv/bin/python
$PY -m builder init-db
$PY -m builder new 2026-11-08        # scaffold week.json from rosters + calendar
$PY -m builder build 2026-11-08      # HTML, PDF, overflow table, proof PNGs
$PY -m builder publish 2026-11-08    # commit + push to main (add --branch X to test)
$PY -m pytest builder/tests -q
```

Environment: `BUILDER_REPO` (clone to build in; defaults to the repo this
code is in), `BUILDER_VAR` (`~/bulletin-builder/var`), secrets in
`~/.config/bulletin-builder/env` (`SESSION_SECRET`, `RESEND_API_KEY`,
`EMAIL_FROM`, `ANTHROPIC_API_KEY`, `PUBLIC_BASE_URL`, `BUILDER_MODEL_CALLS`).

## week.json in one paragraph

`liturgy.items` is the ordered service, each item one of `section`, `plain`,
`scripture`, `responsive`, `corporate`, `hymn`, `music`, `speaker`, `reading`,
`sermon`. `liturgy.notes` are the margin notes keyed by item id. `layout.pages`
says which item ids print on p3–p6; `layout.notes` moves a note into a
neighbouring row with `grid-row: span N` (the fix the knowledge note describes);
`layout.page_style` turns off `spread` or sets `row-gap` per page;
`layout.overrides` holds free CSS the fit loop or chat step records. Everything
else (flowers, announcements, serve, calendar, sermon) is plain content.
