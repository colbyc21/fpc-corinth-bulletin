# Basecamp Bulletin Builder — Implementation Plan

**Purpose.** Move weekly bulletin production from a hands-on Claude Code session on Colby's laptop to a self-service web form on the `basecamp` server, so that a non-technical volunteer can produce, proof, correct and publish the Sunday bulletin without Colby. The server does the deterministic work itself, calls the Claude API only for the parts that need judgment, and enforces a hard monthly spend cap.

**Audience.** This document is written for Claude Code running on basecamp (Ubuntu), working with Colby. It assumes a clone of the `fpc-corinth-bulletin` repo and the knowledge notes in `docs/knowledge/`. Read those four notes before writing any code; they are two months of hard-won rules about the bulletin.

**Status.** Phase 0 and Phase 1 done, 2026-10-29 (see §9). Phase 0 (§11) is a discovery pass that Claude Code runs on the server itself. It finds out nearly everything by looking; the short list of things it has to ask Colby is in §11.3, each with a default so work can continue while waiting.

**How to start.** On basecamp, in the repo clone:

```
claude
> Read docs/basecamp-bulletin-builder-plan.md and docs/knowledge/*.md, then run Phase 0 (§11). Write what you find to docs/basecamp-discovery.md, ask me only the §11.3 questions, and stop before Phase 1.
```

Then, once Colby has answered: `> Proceed with Phase 1 of the plan.` Each later phase is started the same way. Commit at the end of each phase with a message naming the phase.

---

## 1. What the weekly job actually is (observed Aug 30 – Nov 1, 2026)

Every Sunday needs two outputs, built from the same content:

| Output | File | Shape |
|---|---|---|
| Print booklet | `bulletins/<date>/bulletin-<date>-legal.html` + `.pdf` | 14×8.5 in legal landscape, 2 sheets, saddle-stitched → 8 panels (cover, welcome, liturgy p3–p6, sermon notes p7, back cover) |
| Scroll bulletin | `src/scroll/<date>.html` + an entry in `src/_data/scrollBulletins.json` | Self-contained HTML page, published by Netlify at `/scroll/<date>/` |

Inputs that arrive each week, and who produces them:

| Input | From | Format | Regularity |
|---|---|---|---|
| Liturgy | Pastor | `<date> Liturgy.docx` — order of worship, then a `MARGIN NOTES:` block, occasionally a `SERMON OUTLINE:` block | Every week |
| Sermon notes | Pastor | `<date> Sermon Notes.docx` — title, scripture, 1–3 intro paragraphs, numbered points, optional quote | Most weeks; missing on guest-preacher and some communion weeks |
| Content | Office | `bulletin-content-<date>.rtf` — flower dedication, announcements, Those Who Serve, staff, calendar, Pew Bible page, welcome text | Every week; routinely has gaps and typos |
| Pastor's announcements | Pastor | `Announcements.rtf` (email paste) | Occasional; may request an insert |
| Kids bulletin material | Colby, in chat | lesson list, memory verses | Monthly, separate product |

What changes between two consecutive weeks is small: about 240 of 870 lines in the print HTML and about 190 of 1,180 lines in the scroll HTML. The first 363 lines of the print file and 795 of the scroll file are CSS that never changes. **The build is a templating problem with a layout-fitting step at the end, not a writing problem.**

Where the human (currently Claude in a session) actually spends effort, in order:

1. **Fitting the print pages.** Margin notes sit in a right-hand column of a grid; a tall note inflates its row and pushes content off the panel. The fixes are a small set of CSS tricks (`grid-row: span N`, moving an item to the next page, dropping `spread`). This took three render-and-look passes on Nov 1 and two on the kids bulletin. It is the only step that genuinely needs a capable model with eyes on the rendered page.
2. **Parsing the liturgy docx** into structured items (item type, asterisk, hymn number and title, scripture reference, responsive-reading lines, prayer text) and matching each margin note to its item.
3. **Cleaning the content file**: typos, missing Oxford commas, "Deacon's" → "Deacons'", name spellings (see `docs/knowledge/name-spellings.md`), stale month references, blank fields.
4. **Handling the week's special shape**: communion order, Every Member Canvass moment, Monthly Missionary Focus, Reformation Sunday subtitle, a paper insert request.
5. Committing and pushing; Netlify does the rest.

---

## 2. Target experience for the volunteer

One web page, `https://basecamp/bulletin/`, behind a login. Four steps across the top: **Enter → Build → Proof → Publish**.

**Enter.** A form for the next Sunday (date pre-filled with the coming Sunday). Most fields are already populated from the rosters, calendar and standing data in §5; the volunteer confirms or edits. Two file drop zones: Liturgy docx and Sermon Notes docx. Sermon notes may be left empty (the builder then makes a fill-in page).

**Build.** One button. Progress lines appear ("Reading liturgy… Laying out print pages… Rendering PDF…"). Typical build should take under three minutes.

**Proof.** Side by side: the eight print panels as images, and the scroll bulletin in an iframe. Below them, a list the builder wrote of every change it made to submitted text ("changed *cost* to *costs* in the shoebox announcement") and every gap it flagged ("Circle of Prayer is blank"). Under that, a chat box: "Change the assisting elder to Dr. John Shipp." Each message re-renders and refreshes the proof.

**Publish.** A button that commits and pushes to `main`. Netlify builds the site. The PDF is available to download for the office printer. Optionally gated: the volunteer can mark *Ready*, and a reviewer (Colby or the pastor) presses *Publish*.

Everything the volunteer types is saved as they go, so the week can be started Wednesday and finished Friday.

---

## 3. Architecture

The app code lives **in this repo under `builder/`** (one history; Netlify ignores it because Eleventy reads only `src/` and the passthroughs). On basecamp it runs from a separate deploy clone. *(Updated after Phase 0, see `docs/basecamp-discovery.md` §11.2.)*

```
fpc-corinth-bulletin/builder/         # in this repo
├── app/                              # web UI, auth, job queue, spend ledger  (Python 3.12, FastAPI + Jinja2 + HTMX, SQLite)
├── pipeline/                         # the steps in §4 (pure functions where possible)
├── templates/                        # Jinja2 templates for print + scroll (see §4.4; Jinja2, not Nunjucks, so the app has one language)
├── scripts/                          # phase0_seed.py and friends
├── tests/                            # pytest; test_render_golden.py is the Phase 1 acceptance test
├── deploy/                           # bulletin-builder.service (user unit)
└── data/                             # seed/ (rosters, events) and standing/ (staff, welcome, pew pages, hymns)  (§5)

basecamp (user clovis)
├── ~/bulletin-builder/repo/          # deploy clone of this repo, pushes to main over SSH with the deploy key
├── ~/bulletin-builder/var/           # runtime data: SQLite, uploads in progress, renders
├── ~/.config/bulletin-builder/env    # secrets, mode 600 (API key, session secret); no sudo needed
└── (Playwright's bundled Chromium)   # headless rendering + overflow measurement, via the Playwright API
```

Ubuntu notes: the `chromium` apt package on recent Ubuntu is a snap shim, and snap confinement breaks headless printing to arbitrary paths. Use the Chromium that Playwright installs (`playwright install --with-deps chromium`) for both PDF printing and the overflow measurement; it is the same engine with the same print flags used today. Drive it through Playwright's API, not the Chromium CLI, because of the AppArmor restriction found in Phase 0; PNG proofs come from `pymupdf`, not `pdftoppm`. Run the app as **user `clovis` under a systemd *user* unit** (linger is already enabled), the same pattern as the fpc-sermons app. Bind to `127.0.0.1:8710` and expose it as **`bulletin-admin.fpccorinth.org` on the existing host cloudflared tunnel**; adding that hostname is the one step that needs sudo (`/etc/cloudflared/config.yml`). *Done 2026-10-31. Cloudflare Access was tried in front and dropped the same day; the magic-link login is the only gate, as for the sermons app.* No `ufw` change and no new reverse proxy. Keep secrets in `~/.config/bulletin-builder/env` (mode 600), loaded by the unit file. The GitHub deploy key is `~/.ssh/fpc_bulletin_deploy`.

Principles:

- **Deterministic first.** Extraction, templating, rendering, overflow measurement, git, ICS generation are plain code. No model call touches them.
- **Model calls are few, bounded and logged.** Three kinds: *parse* (docx text → JSON), *copyedit* (content → corrected content + change list), *fit/revise* (an agent loop with a tiny tool set, capped at N turns). Every call records `usage` to the spend ledger.
- **The week lives in one JSON file.** `bulletins/<date>/week.json` is the single source of truth. Templates render from it; the chat box edits it; the archive keeps it. Hand edits to HTML are the exception, recorded as *overrides* in the same JSON.
- **The repo stays the record.** `bulletins/<date>/` keeps the docx files, `week.json`, generated HTML and PDF, exactly as today. Google Drive sync is optional (§11).

### 3.1 Model and engine

Use the Claude API directly through the official Python SDK (`anthropic`). Model `claude-opus-5-5` for all three call kinds; `claude-haiku-5-5` is adequate for the copyedit pass if cost ever matters, but at current volume it does not (§8). Default effort `medium` for parse and copyedit, `high` for fit/revise. Enable prompt caching on the system prompt, which should contain the knowledge notes verbatim, the week's JSON schema and the template rules. Include the `fallbacks: "default"` server-side fallback so a classifier refusal does not stall a build.

For the fit/revise loop, give the model exactly four tools: `read_week_json`, `write_week_json`, `set_layout_override(page, selector, css)`, and `render_and_measure()` which returns per-panel overflow in points plus a PNG of each page. Cap the loop at 8 turns per build and 4 per chat message. If the cap is hit, stop and show the volunteer the best result with a warning rather than keep spending.

An alternative engine is the Claude Agent SDK (Claude Code as a library) running inside the repo clone with the same tool restrictions. It is a reasonable choice if the local operator already runs Claude Code on basecamp, because sessions give the chat box memory for free. The direct API is recommended because the tool surface here is small and the spend accounting is simpler. Do not use a local open-weight model for the fit step; the page-fitting judgment is where it will fail and the volunteer will not know.

---

## 4. Pipeline

### 4.1 Intake

Form POST writes:

- `bulletins/<date>/<date> Liturgy.docx`, `<date> Sermon Notes.docx` (if provided)
- `bulletins/<date>/week.json` seeded from the form fields, rosters, calendar and standing data

### 4.2 Extract (deterministic)

docx → plain text with paragraph and tab structure preserved. The one-liner used today:

```
unzip -p file.docx word/document.xml | perl -pe 's/<\/w:p>/\n/g; s/<w:tab\/>/\t/g; s/<w:br[^>]*\/>/\n/g; s/<[^>]+>//g'
```

`python-docx` is fine too. Keep tabs: the choral introit and anthem titles are tab-aligned on the right and sometimes land on the previous line (see the knowledge notes).

### 4.3 Parse and copyedit (two model calls, structured output)

**Call 1 — liturgy + sermon → JSON.** Use `output_config.format` with the `week.json` liturgy schema (§6) so the result is guaranteed valid. The prompt must include the rules from `docs/knowledge/weekly-bulletin-workflow.md` that affect parsing: Greetings & Announcements is a standing item the docx omits; margin notes come only from this week's docx; the Choral Introit title drift; the `SERMON OUTLINE:` fallback; communion order follows the docx, not a template; the Pentecost count for the liturgical day (Pentecost 2026 = May 24) unless the content form's Subtitle is filled.

**Call 2 — content copyedit.** Input: the announcements, flower dedication, Those Who Serve, calendar lines as submitted. Output (structured): the corrected text, a `changes[]` list with before/after and reason, and a `flags[]` list for anything it could not resolve (unclosed quotation, scripture reference that looks wrong, blank role). Rules: apply the name-spellings note; never change the meaning of an announcement; never drop a sentence; serial comma; roles in the house style ("Deacons' Meeting", "Joy Riders", "Children's Church"). Show `changes[]` and `flags[]` to the volunteer on the Proof page.

### 4.4 Render (deterministic)

Two Jinja2 templates, built once from the 2026-11-01 files (`builder/templates/`):

- `print-legal.njk`: the CSS head verbatim; the eight panels as template blocks; the liturgy grid as a loop over items with their notes. Page breaks are data: `week.json.layout.pages` lists which item ids go on p3, p4, p5, p6. Default pagination follows the usual shape (p3: Meditation through Hymn; p4: Invocation, Confession; p5: Assurance through Children's Church; p6: Scripture through Postlude) and is adjusted by the fit loop.
- `scroll.njk`: lines 1–806 verbatim, then the body sections.
- Communion weeks add the `.sacrament-sub` rule; the template includes it conditionally on `week.special.communion`.
- Standing inserts (the Thanksgiving volunteer insert is one) are their own small templates, selected by `week.inserts[]`.

Then: headless Chromium prints the PDF with the flags used today (`--headless --disable-gpu --no-pdf-header-footer --print-to-pdf=…`; add `--virtual-time-budget=15000` for the kids bulletin's web fonts). Run `npm run build` in the repo clone to confirm Eleventy still builds, then `git checkout -- src/css/style.css` (Tailwind rewrites it).

### 4.5 Measure and fit

A Playwright script opens the print HTML at the page size and, for each panel, reports `scrollHeight - clientHeight` and whether any `.lg-note` has pushed the grid past its container. Zero overflow everywhere → done; no model call. Otherwise, the fit loop (§3.1) runs with the measurements and page PNGs, applying the known fixes in order of preference:

1. `grid-row: span N` on the tall note beside a neighbouring item that has no note, deleting the empty note cell it spans into
2. put two notes in one cell when a page has only two items, and drop `spread` on that page
3. move the last item of the page to the top of the next page
4. reduce `row-gap` on that page only

Record whatever it did as `layout.overrides[]` in `week.json` so the next render is reproducible, and so the chat step can see what was done.

### 4.6 Proof

Page PNGs (`pdftoppm` or Chromium screenshot), the scroll page rendered in an iframe from the local build output, `changes[]`, `flags[]`, and the chat box.

### 4.7 Revise (chat)

Each message runs the fit/revise agent with the message, `week.json`, the current measurements and the knowledge notes. It must edit `week.json` (content changes) or `layout.overrides` (layout changes), then call `render_and_measure`. It never edits the templates. It replies with one or two sentences saying what changed. Cap 4 turns per message.

### 4.8 Publish

1. Add the entry at the top of `src/_data/scrollBulletins.json` (date, liturgical_day, sermon_title, url)
2. `git add` the week folder, the scroll file and the JSON index; commit as `Add scrollable bulletin and print booklet for <Month D, YYYY>` with the trailer `Co-Authored-By: Claude <noreply@anthropic.com>`
3. `git push origin main`
4. Poll the Netlify deploy status API for that commit and show *Live* with the URL when it succeeds, or the failure log if it fails
5. Corrections after publish go through the same chat box and commit as `Correct <what> for <date>`

---

## 5. Front-loaded data (so the form is mostly pre-filled)

This is the part that takes the most off Colby's plate week to week, and it is all deterministic.

### 5.1 Volunteer roster

A single table, editable in the app or maintained as a CSV/Sheet the app imports:

| sunday | assisting_elder | greeters | ushers | acolytes | nursery | circle_of_prayer | childrens_church | av_engineer |
|---|---|---|---|---|---|---|---|---|
| 2026-11-01 | Dr. John Shipp | Courtney & Ivy Monaghan | Scott Sawyer; Holland Yeates; … | Jack Windsor & Will Sanders | … | Jason & Jennifer LeGoff | Jennifer Shipp | Russell Smith |

The office keeps a schedule for the whole year that changes ad hoc when people are away, so there is no clean quarterly file to import. **Decision (§11.3, item 7): the app owns the roster table**, seeded from `builder/data/seed/rosters.csv` (22 Sundays), and the volunteer confirms or edits each week by hand. A yearly-schedule import can come later. The builder reads the row for the week into `week.serve` and shows it on the form for confirmation. Names pass through the name-spellings check. Roles with no entry stay visibly blank on the form so the volunteer notices, and the builder flags them rather than guessing.

Keep the staff list as standing data with an edit screen, not as a weekly field. It has not changed since August.

### 5.2 Church calendar and ICS feed

One events table (`title, start, end, location, notes, category, recurrence`). Sources, in order of preference:

- **Decision (§11.3, item 6): the app owns the table.** The church has Google Workspace but no main calendar that anyone knows of. The app keeps the events table with an edit screen, seeded from `builder/data/seed/events.csv` (74 lines from past bulletins) plus the recurring items that appear every week: Messy Bible Study Tuesday 8 a.m., Kids Quest / Youth House / Bible Study Wednesday 6 p.m., Session and Deacons' meetings, First Thursday at the Youth House, Joy Riders, Day School closures.
- If a church Google Calendar is set up later, the app can either subscribe to its ICS as the source (read-only) or keep publishing its own ICS for Google to subscribe to. Keep the events table behind an interface so that swap is local.

From that table the build produces two things:

1. The **Church Calendar** box on the back cover: events from the bulletin's Sunday through the following Sunday, in the house format (`Nov 3 - Messy Bible Study at 8 am`). The volunteer can add or remove lines for the week without touching the master table.
2. **`calendar.ics`** written into `src/` and published by Netlify at `https://bulletin.fpccorinth.org/calendar.ics`, so members can subscribe from their phone calendar. Also publish `/calendar/` as a simple page listing the next 60 days with a "Subscribe" button and instructions for iPhone and Google Calendar. Regenerate the ICS on every publish; events get stable UIDs so subscriptions update instead of duplicating.

If the church uses Planning Center or Breeze, check first whether either already exposes rosters and events; importing from there beats maintaining a second copy.

### 5.3 Standing text and lookups

- Welcome paragraphs, with the two Children's Church variants (regular schedule / school break) and a custom option
- Vision & mission text, contact block
- Pew Bible page table: book + chapter → page. The pew Bible is the **ESV Thinline** (§11.3). Seeded with 16 references in `builder/data/standing/pew-bible-pages.json`; fill in the rest from that edition's page map.
- Hymnal table: number → title, so a hymn number alone fills the title and a mismatch is flagged. The hymnal is **The Hymnal for Worship & Celebration** (brown, Word Music 1986, Fettke and Barker; confirmed by Colby 2026-10-31). `builder/data/standing/hymns.json` holds all 628 numbers plus the Amens (628a–628i): `first_line` and `tune` from hymnary.org, `title` and `seen` from the church's own liturgy files for the 52 numbers sung since February, otherwise the first line with `title_source` marking it. Keys starting with `_` are metadata. Hymnary notes its index is unproofed, so a mismatch is a flag, not an error.
- Series tracker: current sermon series title and preacher default (Rev. Waring Porter)
- Monthly items: Missionary Focus speaker for the month, kids bulletin month

---

## 6. `week.json` schema (abridged)

```json
{
  "date": "2026-11-01",
  "liturgical_day": "Twenty-third Sunday after Pentecost",
  "subtitle_override": null,
  "special": { "communion": false, "canvass": false, "missionary_focus": { "name": "Courtney Monaghan", "role": "Missions Committee Member" } },
  "pew_bible_page": 398,
  "welcome_variant": "regular",
  "flowers": { "text": "…", "scripture": "…", "reference": "Lamentations 3:22-23", "signed": "Anna, Ben & Chandler Erwin" },
  "announcements": [ { "title": "37th Community Thanksgiving Meal", "body": "…", "when": null, "where": null } ],
  "serve": { "assisting_elder": "Dr. John Shipp", "greeters": "…", "ushers": ["…"], "acolytes": "…", "nursery": ["…"], "circle_of_prayer": "…", "childrens_church": "…", "av_engineer": "…" },
  "calendar": [ { "date": "2026-11-03", "text": "Messy Bible Study at 8 am, Election Day (Fellowship Hall in use) / No Day School" } ],
  "liturgy": {
    "items": [
      { "id": "prelude", "type": "music", "title": "The Piano Prelude", "star": false },
      { "id": "meditation", "type": "scripture", "title": "Call to Meditation", "reference": "Lamentations 3:22-23", "text": "…" },
      { "id": "hymn1", "type": "hymn", "number": 10, "title": "O Worship the King", "star": true },
      { "id": "confession-call", "type": "responsive", "title": "Scriptural Call to Confession", "reference": "taken from Daniel 9", "lines": [ { "who": "Leader", "text": "…" } ] }
    ],
    "notes": [ { "for": "hymn1", "label": "O Worship the King", "text": "…" } ]
  },
  "sermon": { "title": "Spiritual Renewal: When God Begins to Work", "scripture": "Nehemiah 1:1-11", "preacher": "Rev. Waring Porter", "intro": ["…"], "points": [ { "heading": "I. God Awakens His People (vv. 1-4)", "quote": null } ], "notes_lines_per_point": 3 },
  "inserts": ["thanksgiving-meal"],
  "layout": { "pages": { "p3": ["prelude", "…"], "p4": ["…"], "p5": ["…"], "p6": ["…"] }, "overrides": [] },
  "copyedit": { "changes": [], "flags": [] }
}
```

Write the full JSON Schema in `pipeline/schema/week.schema.json` and validate on every write.

---

## 7. Access, auth and safety

- **Nothing works without a login.** Every route except the sign-in page and the public calendar page requires an authenticated session: the form, both file uploads, Build, the proof images, the chat box and Publish. Unauthenticated requests get a redirect to sign-in, never a partial page. Uploads are additionally limited to `.docx` under 10 MB, checked by content type, and stored only under the week's folder.
- **Decided (§11.3, item 2; revised 2026-10-31):** basecamp is internet-reachable through the host cloudflared tunnel, so the app sits at `bulletin-admin.fpccorinth.org`. Identity and roles use the **sermons-app magic-link pattern** and nothing else: Cloudflare Access was set up in front and removed the same day as unnecessary friction, since the Console spend limit already caps the cost of a break-in and the sermons app has run this way. Inside the app, the magic link through the same Resend account, so gmail and proton users work and there are no passwords to manage. Google sign-in was dropped because most users are not on `@fpccorinth.org` accounts.
- Three roles: *editor* (enter, upload, build, proof, chat, mark ready), *publisher* (everything an editor can do, plus Publish) and *admin* (everything, plus user management, rosters, calendar and standing-text screens, the spend cap and the kill switch). Start with the office administrator as editor, the pastor and Colby as publishers, and Colby as admin (§11.3, item 1). Editors mark *Ready*; only publishers press Publish (item 3). Accounts are created by an admin; there is no self-signup.
- Every login, upload, build, chat message and publish is written to an audit log with the user and timestamp, so a wrong bulletin can be traced to the step that introduced it.
- The git push uses the repo-scoped deploy key `~/.ssh/fpc_bulletin_deploy` generated in Phase 0 and pushes straight to `main` (§11.3, item 4). Never Colby's personal `gh` token.
- Uploaded files are untrusted: store them under the week folder, never execute anything from them, parse with the extractor only.
- The app never deletes from the repo. Re-building a week overwrites that week's generated files only.

---

## 8. Spend control

Measured from the last six weeks of sessions (Sep 27 – Nov 1), re-priced at API list rates:

| Model | Per routine week | Per heavy week | Six weeks |
|---|---|---|---|
| Opus 5.5 | $2.30 – $3.60 | $10.30 | $28 |
| Sonnet 5.5 | $1.40 – $2.40 | $7.20 | $19 |

Those numbers include a full Claude Code system prompt and tool catalog on every call; this app sends far less per call, so expect lower. Budget **$40 per month** and it will not be reached in a normal month.

Four layers, all of them required:

1. **Console workspace limit (the real safety net).** Colby creates a dedicated Anthropic Console workspace `bulletin-builder` with its own API key, a **$40** monthly spend limit and email alerts (confirmed, §11.3 item 5). The key lives in `~/.config/bulletin-builder/env` (mode 600). This is enforced by Anthropic and cannot be exceeded by a bug in the app.
2. **App ledger.** Every API response's `usage` (input, cache write, cache read, output) is written to SQLite with the job id and priced at the current rate card. The form header shows *This month: $X of $40*. At 80 percent, builds still run but the chat box warns; at 100 percent, the app refuses new model calls and tells the volunteer to contact Colby. Deterministic steps keep working.
3. **Per-job caps.** Fit loop ≤ 8 turns and ≤ $3; chat message ≤ 4 turns and ≤ $1; ≤ 3 full builds per week per bulletin; ≤ 30 chat messages per bulletin. Hitting a cap stops cleanly and says so.
4. **Kill switch.** An environment variable `BUILDER_MODEL_CALLS=off` makes every model step a no-op that returns the previous result, so the deterministic pipeline can still render and publish during an incident.

Also: prompt caching on the system prompt (knowledge notes + schema + template rules) cuts the repeated-context cost to a fraction; verify `cache_read_input_tokens` is non-zero in the ledger after the second call of a job.

---

## 9. Phases

**Phase 0 — Discovery (Claude Code on basecamp, §11).** `docs/knowledge/*.md` already holds the four notes Claude kept outside the repo. Claude Code inventories the server, verifies access, writes `docs/basecamp-discovery.md`, asks Colby the §11.3 questions, and updates §3 and §7 of this plan with what it found. Acceptance: a test render of `bulletins/2026-11-01/bulletin-2026-11-01-legal.html` to PDF on the server matches the committed PDF's page size and fonts, and a test commit to a scratch branch reaches GitHub.

**Phase 1 — Deterministic core (first).** Templates from 2026-11-01, `week.json` schema, extractor, renderer, PDF, overflow measurement, git publish, ICS generation, roster and calendar tables with edit screens. Acceptance: regenerate the Nov 1 bulletin from a hand-written `week.json` and get a pixel-near-identical PDF and scroll page; publish to a test branch.

> **Added 2026-10-31.** Publishers can edit the calendar; events may repeat monthly on the nth weekday (`MONTHLY;BYDAY=1SU`). The content file and pastor's announcements are optional uploads: the Details form carries everything they did, and saving it with the copyedit box ticked runs the content copyedit over what changed. A hymn index (`/hymns/` on the site, Hymns in the app) is read from the published scroll pages: times sung, last sung, every date, and a hymnary.org link for the words.

> **Done 2026-10-29.** `bulletins/2026-11-01/week.json` was hand-written and regenerates the booklet with identical text (the confirmed LeGoff spelling is the one change), 0.00–0.01 % pixel difference against the committed HTML rendered on the same box, and zero overflow on all eight panels; the scroll page differs only by the same correction. A publish to `basecamp-phase1-test` reached GitHub as commit 73fbefd and the branch was deleted. Also delivered: `src/calendar.ics` + `/calendar/` page, the FastAPI app with magic-link login and admin screens (rosters, events, standing text, users, audit), 44 passing tests, and the user unit. The volunteer form and chat are Phase 3; in Phase 1 the week page edits `week.json` directly. Rosters, events and standing text are admin-only screens, as §7 says; editors change the week's roster on the week itself.

**Phase 2 — Model steps.** Parse, copyedit, fit loop, spend ledger and caps. Acceptance: build Oct 18 (communion, no sermon notes), Oct 25 (Reformation Sunday subtitle, canvass after sermon, insert) and Nov 1 (long responsive reading, missionary focus) from their original inputs with no human edits and zero overflow; total cost for the three under $10.

> **Done 2026-10-31.** `builder/scripts/phase2_acceptance.py` rebuilt all three Sundays in a scratch clone from the docx and rtf inputs only. All eight panels measured zero overflow for each week and every margin note printed. Per week: Oct 18 parse+content $0.33, fit 7 turns $0.52 (the communion page needed the Lord's Supper note spanned six rows and p6's row gap set to 0); Oct 25 $0.34 with no fit needed; Nov 1 (parsed *without* the worked example, since it is the example) $0.33, fit 3 turns $0.12, which found the same p4 fix the hand layout used (both notes in one two-row cell, spread off) and rebalanced the back cover. **Total for the three, including the two retries that found bugs: $2.15.** Prompt caching showed in the ledger (cache reads on every second call within five minutes). Differences from the hand-made booklets are style (announcement wording, flower sign-offs, "Sermon Notes" vs "Sermon Outline" title) or input drift (the committed Oct 25 content rtf has a different flower dedication than the booklet that was printed; the Nov 1 form said "Dr. Don Shipp"). Bugs the run exposed and fixed: a spanning note cell could swallow a neighbour's note or cross a section heading (now rejected by `week.validate`); the fit loop could not touch the back cover (it now sets `announcement_columns` and sees any overflowing panel); hymn verse selections ("vv. 1–3") went into a field the hymn line does not print; the copyeditor held an announcement out on a timing judgment (the prompt now says include and flag). Delivered: `pipeline/model.py` (one API path: streaming, structured output, adaptive thinking, the `fallbacks: default` beta, ledger pricing, kill switch, monthly and per-job caps), `parse.py`, `content.py`, `intake.py`, `fit.py`, the three prompts in `builder/prompts/`, the upload + Parse inputs form and fit-aware Build on the week page, the `/spend` page, `intake` / `build --fit` / `spend` CLI commands, and 55 passing tests (the model is faked in tests; `conftest.py` hides the real key). The volunteer form, chat revise and Netlify status remain Phase 3.

**Phase 3 — Volunteer UI and chat.** Form with pre-fill, proof page, chat revise, roles, Netlify status. Acceptance: someone who has never seen the repo builds and publishes a real Sunday with Colby watching but not touching.

> **Built 2026-10-31; the human acceptance is still to run.** The week page is the four-step flow of §2 (Inputs → Details → Build & proof → Publish). *Inputs*: the four file slots and "Read the files". *Details*: cover subtitle, communion / canvass, Children's Church wording, Pew Bible page, the sermon line, flowers (with the longer-dedication fields folded away), announcements with print-only / phone-only extras, Those Who Serve with "Refresh from roster", and the calendar lines with "Refresh from calendar"; the order of worship is listed read-only and changed through the chat. *Build & proof*: the panels, the scroll iframe, the edits the builder made, the name corrections and the "please check" flags. *Chat*: `pipeline/chat.py`, one job per message (4 calls, $1), tools `read_week`, `edit_week` (one value by JSON pointer, name rules re-applied, schema-validated), `set_layout`, `render_and_measure`; the thread is kept in the `chat` table and shown on the page; 30 messages per bulletin. *Publish*: Mark ready (editor) and Publish to main (publisher), then a status line that polls `/weeks/<date>/deploy.json`: the Netlify API when `NETLIFY_TOKEN` and `NETLIFY_SITE_ID` are in the env file, otherwise the live scroll page's `<meta name="fpc-build">` marker, which the publish step records as `weeks.published_build`. The raw JSON editor survives under "Advanced". An end-to-end run through the app on the deploy clone (form save → build → chat "Add 'Choir rehearsal, 7 p.m.' to Wednesday, November 4" → publish to `basecamp-phase3-test`, 24 s, $0.08) did what was asked; the branch was deleted afterwards. 61 tests pass. **What remains for the acceptance:** Colby adds a Netlify token to the env file if he wants deploy failures surfaced (the marker check only says "live" or "still deploying"), and a volunteer runs a real Sunday through bulletin-admin.fpccorinth.org.

**Phase 4 — Extras.** Kids bulletin (monthly, letter size, generator scripts need to move into the repo), special inserts library, email of the PDF to the office printer, Planning Center or Breeze import if applicable.

**Phase 5 — The Friday Email (proposed 2026-10-31).** Colby sent eleven weeks (Aug 21 – Oct 30) of the office's Friday-email content file, the finished `email.html`, and one August content text used as the worked example; they are on basecamp under `~/incoming/friday-email/` (kept out of the repo: they carry member names and phone numbers). What they show: the office writes a plain-text/RTF file with the same headings each week (Opening Scripture; Prepare / Give / Connect; Did You Know?; Announcements; Children's, Youth, Women's Ministry; Serve; Birthdays; Anniversaries; The Church Calendar; Those Who Serve this Sunday; Closing Hymn), with hosted image URLs pasted inline and a note to carry forward still-current items from last week. The finished email is a hand-built 600 px table layout with inline styles in the site's palette and fonts, a logo header, section bands, button links, a "View Sunday Bulletin" button to bulletin.fpccorinth.org, and a footer with the podcast and Facebook links; it is pasted into Constant Contact as custom code. Design: (1) `friday.json` per week beside `week.json`, sharing the calendar, serve and announcements; (2) a content model call like `content.py` that turns the office file plus last week's `friday.json` into the structured sections, carrying forward only what is still dated in the future and flagging the rest, with the name rules applied (the Oct 30 sample shows why: the ushers line gained a name the content file did not have, and "Ivey" became "Ivy"); (3) a Jinja email template reproducing the sample's layout, rendered to HTML and proofed in the app as a 600 px and a 375 px preview; (4) a push step using Constant Contact's v3 API: device-flow OAuth with `offline_access` (refresh token in the env file, rotated on use), `POST /emails` with `format_type` 5 and the HTML, `PUT` the contact list, `POST .../tests` to the office, and Schedule left inside Constant Contact for the first weeks. Needs from Colby: a v3 app in the Constant Contact developer portal with the device flow enabled (client ID into the env file), and the list name. Acceptance: rebuild Oct 23 and Oct 30 from their content files with no human edits and get emails whose text matches the sent ones apart from house style, then a real Friday pushed as a draft, test-sent, and sent from Constant Contact by the office.

**Phase 5 build (2026-10-31).** Built as planned, with the web form in place of the content file as the main path, matching the bulletin: `/weeks/<sunday>/email` has four steps. (1) *Notes*: optional upload of the office's .rtf/.txt or a zipped .rtfd (its pictures go to a photo shelf), read by one `content`-kind model call (`friday_content.py`, `prompts/friday.md`) and merged with the calendar table (two weeks) and the roster, disagreements flagged. (2) *Content*: the whole email as a form, one card per section with add/remove/move item buttons, each item with a photo input (`media.py`: resized to 1200 px, JPEG or PNG, named by content hash under `var/media`, served without login at `/media/<name>` so mail clients can fetch it) plus caption, width and position; birthdays, anniversaries, Those Who Serve and the calendar as line-per-entry textareas with refresh-from-the-tables buttons; a copyedit checkbox that runs the same model pass on what changed. Saving renders `friday-<sunday>.html`. (3) *Preview*: the rendered email at 620 and 390 px, and a chat box (`friday_chat.py`, tools `read_email`/`edit_email`/`render_email`, 30 messages per email). (4) *Send* (publisher): creates the Constant Contact draft addressed to the members list and test-sends to the addresses typed (`friday_push.py`); a second form schedules the send, disabled while the content is newer than the draft. `FRIDAY_FROM_EMAIL` (the verified sender) goes in the env file; `FRIDAY_FROM_NAME`, `FRIDAY_REPLY_TO` and `CONSTANT_CONTACT_LIST` are optional. CLI: `python -m builder email new|intake|build|push <sunday>`. Acceptance run 2026-10-31 (scratch clone, $1.12 over four calls including two failed schema attempts): rebuilding Oct 23 from its notes with the Oct 30 worked example reproduces the sent email's text apart from house style, item splitting, and what the office added by hand in Constant Contact (a Bow-Making Class, a membership-class calendar line); the calendar and Those Who Serve come from the tables, so differences there are table content, which the flags point at. Rebuilding Oct 30 with no worked example (it is the example) and the rebuilt Oct 23 as last week is rougher but complete: everything the notes carried is present, items keep the office's order, carried-forward items keep their text, nursery rooms sit under Nursery. The sent Oct 30 email also had a Book Club item, a sentence about the online form, and four dated calendar lines that were not in the notes at all. Two API limits found and fixed on the way: a strict output schema may hold at most 16 union-typed (nullable) fields and no deep nesting, so the model answers in a flat, null-free shape. Still to do: on the first test send confirm Constant Contact appends its unsubscribe footer to a custom-code campaign.

---

## 10. Acceptance tests to keep

- Golden builds for 2026-09-20 (communion), 2026-10-04 (guest preacher, outline in liturgy), 2026-10-25 (subtitle, canvass, insert), 2026-11-01 (regular, long reading). Any template or pipeline change must still fit all four with zero overflow.
- Name-spellings check must correct every entry in `docs/knowledge/name-spellings.md` when fed the misspelled forms.
- Spend ledger must refuse a call when the month total is at the cap, and the deterministic publish must still succeed with `BUILDER_MODEL_CALLS=off`.
- ICS validates (`icalendar` library round-trip) and subscribing in Google Calendar shows the seeded weekly events.

---

## 11. Phase 0 — Discovery on basecamp (run by Claude Code)

The goal of this phase is to replace guesses in this plan with facts about the server, using the server itself, and to leave Colby with as few questions as possible. Work through §11.1 and §11.2 without asking anything; record every finding in `docs/basecamp-discovery.md` with the command used and its output. Only then ask the §11.3 questions, all at once, in one message.

### 11.0 Already known (checked from Colby's Mac over SSH, 2026-10-29)

Verify these rather than rediscovering them; update anything that has changed.

- Ubuntu 24.04.4 LTS, kernel 6.8, 915 GB root disk at 13% used, RTX 3090 (24 GB). 126 apt updates and a reboot were pending on 2026-10-29.
- Addresses: LAN `192.168.6.21` (interface `enp42s0`; the old `192.168.1.85` is stale), Tailscale `100.105.57.81`, MagicDNS `basecamp.tailfe9d52.ts.net`. Login user `clovis` (sudo). Tailscale node key expired 2026-08-30 and was re-authenticated 2026-10-29 with `--operator=clovis --ssh`; **Tailscale SSH is on**, so `ssh basecamp` from a tailnet device needs no password or key. Key expiry should be disabled for this node in the admin console.
- Toolchain present: Claude Code 2.1.66 (logged in via claude.ai), git 2.43, Node 22.22, npm 10.9, Python 3.12.3, Docker 29.5. No nginx, no Caddy, no repo clone yet.
- Already running: the services listed in the next two bullets, plus a Docker stack `lifeos` (frontend, backend on 8000, Postgres on 127.0.0.1:5432, and a `cloudflared` tunnel, so there is an existing Cloudflare Tunnel that exposes something to the internet), Home Assistant (8123), zwave-js-ui, Mosquitto (1883), Open WebUI, Ollama (11434), plus services on 3000, 3001, 8080, 8091, 8989 and Tailscale Serve on 443/8443/8444. Do not disturb any of these. Pick a free port for the bulletin app (e.g. 8710) and expose it either through Tailscale Serve (tailnet-only, simplest) or as a new hostname on the existing Cloudflare Tunnel with Cloudflare Access in front (if volunteers need it off the tailnet). That choice is §11.3 item 2.
- **The sermons app is the pattern to copy.** `sermons.fpccorinth.org` is served by a native Node app (`~/fpc_sermons`, `node --env-file=.env dist/server.js`, port 3001) run as a *user-level* systemd unit, `~/.config/systemd/user/fpc-sermons.service`, and exposed through the host-level `cloudflared.service` whose ingress is in `/etc/cloudflared/config.yml`. Deploy the bulletin builder the same way: a user unit under `~/.config/systemd/user/`, a hostname such as `bulletin-admin.fpccorinth.org` added to that ingress (with Cloudflare Access in front if it is reachable from the internet), or Tailscale Serve if tailnet-only. The Docker container `fpc_sermons-app-1` in that folder is a stale alternative deployment; leave it stopped (it wants port 3000, which `zwave-js-ui` already uses for its websocket server).
- Other user services on the box: `basecamp.service` and `trinity.service` (Telegram bots) and `openclaw-gateway.service`. Do not touch them.
- Postgres already exists in the `lifeos` stack, but keep the bulletin app on SQLite; it is one small table set and should not depend on another project's database.
- Claude Code on the box runs on Colby's claude.ai subscription, which is fine for building the app. The app's own model calls must use the dedicated Console workspace API key (§8), not the subscription.

### 11.1 Inventory the server (no questions needed)

Discover and record each of these. Suggested commands in parentheses; use whatever works.

- **OS and resources**: Ubuntu release, kernel, CPU, RAM, free disk on the partition that will hold the repo and PDFs, GPU model and driver (`lsb_release -a`, `nproc`, `free -h`, `df -h`, `nvidia-smi`). The GPU is not needed for this app; note it so the Whisper and open-model services it already serves are not disturbed.
- **What already runs**: listening ports and their owners (`sudo ss -tlnp`), systemd services (`systemctl list-units --type=service --state=running`), Docker containers if Docker is present (`docker ps`), and the reverse proxy in use (look for nginx, Caddy, Traefik, or a Docker proxy; read its site configs to learn the hostname scheme and where to add a `/bulletin/` or `bulletin.` route).
- **Existing sermon/upload app**: find it, note its language, how it authenticates users, how it is deployed, and whether it already has a login the bulletin app could share. Reusing its auth is preferable to building a second one.
- **Network reach**: is the box on the church LAN only, on Tailscale (`tailscale status`), or reachable from the internet (public DNS name, port forwards, existing TLS certificates under `/etc/letsencrypt` or Caddy's data dir)? This decides the login method default in §11.3.
- **Toolchain**: Python 3 version and whether `venv` works, Node and npm versions (Eleventy 3 needs Node 18+), git version, whether `fonts-liberation` and the other Playwright system deps install cleanly, `pdftoppm` availability (`poppler-utils`), and `textutil` absence (it is macOS-only; use `python-docx` or the unzip/perl extractor for docx, and `unrtf` or `striprtf` if any RTF still arrives).
- **Rendering check**: install Playwright's Chromium in a venv, print `bulletins/2026-11-01/bulletin-2026-11-01-legal.html` to a PDF in a scratch directory, and confirm `MediaBox [0 0 1008 612]` and the embedded fonts match the committed PDF (`strings pdf | grep -o "MediaBox *\[[^]]*\]"`, `strings pdf | grep /BaseFont`). Fonts that differ from the Mac render are the most likely surprise on Ubuntu; record which families the committed PDF embeds and install the matching packages (EB Garamond and Work Sans are on Google Fonts; the print template may rely on Mac system fonts, in which case add `@font-face` rules to the template rather than depending on the OS).
- **Eleventy build**: `npm ci` then `npm run build` in the clone, then `git checkout -- src/css/style.css`. Record build time and any warnings.
- **GitHub access**: the repo is `https://github.com/colbyc21/fpc-corinth-bulletin.git`. Check for an existing deploy key or token on the box (`~/.ssh`, `gh auth status`, environment). If none, generate an ed25519 deploy key for this repo and ask Colby to add it in §11.3 (write access). Test with a commit to a scratch branch `basecamp-test`, then delete the branch.
- **Netlify**: confirm the site builds from `main` by reading `netlify.toml`; the site id and a personal access token for deploy-status polling are a §11.3 question. Polling is optional in Phase 1.
- **Calendar sources**: look for anything already on the box or in the repo that holds church events or rosters (a Google Calendar id in an existing app's config, a Planning Center or Breeze integration, CSVs). Look in the shared-drive mirror if one exists on the server.
- **Existing data to seed**: from the repo's `bulletins/*/bulletin-content-*.rtf` files, extract every Those Who Serve block and every Church Calendar block since 2026-06-01 into `data/seed/rosters.csv` and `data/seed/events.csv`. This gives the roster screen and the calendar a real history on day one and shows the volunteer the expected format. Apply `docs/knowledge/name-spellings.md` while extracting. Also extract the staff block (it has been stable since August) into `data/standing/staff.json`, the welcome paragraphs and both Children's Church variants into `data/standing/welcome.json`, and the Pew Bible page mentions into `data/standing/pew-bible-pages.json`.
- **Hymnal**: the liturgy files cite hymn numbers with titles every week; build `data/standing/hymns.json` from every `#N "Title"` pair in `bulletins/*/*Liturgy.docx` since 2026-02-15. That is enough to start; the full table can be filled in later from the hymnal Colby names.
- **Anthropic access**: check for `ANTHROPIC_API_KEY` or an `ant auth status` profile on the box. Do not create keys; the dedicated workspace and its key are a §11.3 item.

### 11.2 Decide defaults from what you found

Write a short "Decisions" section in `docs/basecamp-discovery.md` with your proposed answer for each of these, based on the inventory, so Colby can accept with a yes:

- Hostname and path for the app (reuse the existing proxy's scheme)
- Login method (see §11.3, item 2, with the default chosen from network reach)
- Where the repo clone and data directory live, and which system user runs the service
- Whether the existing sermon app's auth can be shared
- Which fonts need installing or embedding for the print template
- Whether any calendar or roster source was found to import from

### 11.3 Ask Colby, once, all together

Ask these in a single message. Each has a default; if Colby does not object, proceed with the default.

1. **Users.** Who will use the form? Default: the office administrator as editor, the pastor and Colby as publishers, Colby as admin. The pastor keeps writing the Liturgy and Sermon Notes in Word; nothing changes for him.
2. **Login.** Default: if the box is on Tailscale or LAN only, a simple username and password per user managed by the admin screen; if it is internet-reachable, Google sign-in restricted to `@fpccorinth.org`. Say which you found and which you propose.
3. **Publish rights.** Default: editors can mark *Ready*; only publishers press Publish. Ask whether the volunteer should be allowed to publish directly instead.
4. **GitHub.** Give Colby the public deploy key you generated and ask him to add it to the repo with write access (Settings → Deploy keys). Default: the server pushes straight to `main`, the same as today.
5. **Budget.** Default: Colby creates a Console workspace named `bulletin-builder` with a $40 monthly spend limit and alerts to his email, and puts its key in `~/.config/bulletin-builder/env` (was `/etc/bulletin-builder.env`; changed in Phase 0 so nothing needs sudo). Ask for the key only after the workspace exists.
6. **Calendar source.** Ask whether the church already keeps a Google Calendar, Planning Center, Breeze, or similar. Default if none: the app owns the events table, seeded from §11.1, and publishes `calendar.ics`.
7. **Rosters.** Ask for the current quarter's volunteer schedule in whatever form the office produces it (spreadsheet or Word). Default until it arrives: the roster screen starts from the seeded history and the volunteer fills each week by hand.
8. **Pew Bible and hymnal.** Ask for the pew Bible edition (publisher and year) and the hymnal name and edition. Default: use the seeded page and hymn tables and flag anything missing. *Answered: ESV Thinline; The Hymnal for Worship & Celebration (Word Music 1986), confirmed 2026-10-31.*
9. **Google Drive mirror.** Ask whether the server should keep copying each week's folder into the shared drive (needs `rclone` and a service account). Default: no; the git repo is the record, and Colby's laptop clone still syncs to Drive when he pulls.
10. **Printing.** Ask who prints the booklet and whether the PDF should be emailed somewhere on publish. Default: a download button only.
11. **Kids bulletin.** Default: Phase 4, not the first release.
12. **Netlify.** Ask for the site id and a personal access token if deploy-status polling is wanted. Default: skip polling; show the commit hash and a link to the Netlify deploys page.

Record the answers in `docs/basecamp-discovery.md`, update §3, §7 and §8 of this plan to match, commit, and stop. Phase 1 starts on Colby's go-ahead.

**Done 2026-10-29.** All twelve answers are in `docs/basecamp-discovery.md` §11.3; §3, §5, §7 and §8 above reflect them.
