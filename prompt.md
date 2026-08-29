# FPC Corinth Digital Bulletin

Mobile-first digital Sunday bulletin for First Presbyterian Church of Corinth, MS, served at `bulletin.fpccorinth.org`. The site is a companion to the printed bulletin — designed to be read on a phone during worship.

This file is the current-state reference for the project (formerly the original spec). Use it as the entry point when picking up the codebase.

## Tech Stack

- **Static site generator:** [Eleventy 3](https://www.11ty.dev) (`@11ty/eleventy ^3.0.0`)
- **CSS:** Tailwind CSS 3 (compiled to `src/css/style.css`)
- **CMS:** Decap CMS (formerly Netlify CMS), authenticated via Netlify Identity / Git Gateway
- **Hosting:** Netlify (custom domain `bulletin.fpccorinth.org`)
- **PWA:** `manifest.json` + `sw.js` for installable / offline behavior

## Scripts (`package.json`)

```bash
npm run dev        # Eleventy serve + watch (used during local development)
npm run build      # Build Tailwind CSS, then run Eleventy → _site/  (Netlify uses this)
npm run build:css  # Just rebuild Tailwind in watch mode
```

Eleventy reads from `src/` and writes the production build to `_site/`. The `_site/` folder is gitignored — only Netlify needs it.

## Directory Map

```
.
├── .eleventy.js              # Eleventy config: passthrough copies, collections, filters
├── netlify.toml              # Netlify build config + redirects
├── package.json              # Eleventy + Tailwind dev deps
├── tailwind.config.js        # Tailwind theme (colors, fonts)
├── admin/                    # Decap CMS
│   ├── config.yml            # CMS schema (fields → markdown frontmatter)
│   └── index.html            # Loads Decap CMS UI
├── src/
│   ├── _data/
│   │   └── scrollBulletins.json   # Index of HTML "scroll" bulletins (date, sermon, url)
│   ├── _includes/layouts/
│   │   ├── base.njk          # Site shell: header, footer, dark-mode bootstrap
│   │   └── bulletin.njk      # Bulletin layout used by markdown bulletins
│   ├── bulletins/            # Markdown bulletins (Decap CMS writes here)
│   │   ├── bulletins.json    # Defaults: layout=bulletin.njk, permalink=/<date>/
│   │   └── 2026-01-25.md     # Sample/test bulletin
│   ├── scroll/               # HTML "scroll" bulletins (one per Sunday)
│   │   └── YYYY-MM-DD.html
│   ├── css/
│   │   ├── input.css         # Tailwind entry
│   │   └── style.css         # Compiled output (passthrough-copied to _site)
│   ├── images/               # Logos, favicons, uploads/ for CMS media
│   ├── js/main.js            # Dark-mode toggle, share/follow-along buttons
│   ├── about.njk             # /about/ page
│   ├── archive.njk           # /archive/ — combined list of all bulletins
│   ├── index.njk             # / — redirects to the most recent bulletin
│   ├── 404.njk
│   ├── manifest.json         # PWA manifest
│   ├── sw.js                 # Service worker
│   └── Be Blessed.mp3        # Audio used by some bulletins (mapped to /audio/be-blessed.mp3)
├── bulletins/                # SOURCE working folder (not built into the site)
│   └── YYYY-MM-DD/           # Per-Sunday source: .docx, print booklet HTML/PDF, content txt
├── logo.png, qrcode.png      # Passthrough-copied to /images/
├── connect-qr.png, Liturgy Notes.pdf
└── _site/                    # Build output (gitignored)
```

## The Two Bulletin Formats — Important

This project supports two parallel formats for a weekly bulletin. Both ship together, both show up in the archive, and the homepage redirect picks the one with the newest date.

1. **Markdown bulletins** — `src/bulletins/*.md` with YAML frontmatter, rendered by `bulletin.njk`. Permalink `/YYYY-MM-DD/`. This is the format Decap CMS edits (see `admin/config.yml`). Designed to be filled in by non-technical staff via the CMS UI.

2. **Scroll bulletins** — full self-contained HTML pages in `src/scroll/YYYY-MM-DD.html`, passthrough-copied to `/scroll/YYYY-MM-DD/`. These have their own embedded styling (brand sage/ink palette, Work Sans + EB Garamond typography, cover/scroll layout). Each scroll bulletin must also be registered in `src/_data/scrollBulletins.json` so the archive list and homepage redirect know about it.

**Current practice:** Every weekly bulletin since 2026-02-15 is a scroll bulletin. The markdown path still works and the CMS is wired up, but only `2026-01-25.md` exists as a sample. When adding a new week, follow the scroll workflow below unless you specifically want to use the CMS.

## Adding a New Weekly Bulletin (Scroll Format)

1. Copy the most recent file from `src/scroll/` (e.g., `2026-06-14.html`) to `src/scroll/<new-date>.html`.
2. Edit the new file: update date, liturgical day, sermon, hymns, scripture, anthem, prayers, announcements, flowers, "those who serve," calendar.
3. Add a new entry at the **top** of `src/_data/scrollBulletins.json` (newest first):

   ```json
   {
     "date": "2026-06-21",
     "liturgical_day": "Fourth Sunday after Pentecost",
     "sermon_title": "<title>",
     "url": "/scroll/2026-06-21/"
   }
   ```

4. (Optional, but matches existing practice) Create a working folder `bulletins/<new-date>/` and drop the Word docs and any generated print booklet there. This folder is **source material** — it is not built into the site, but it is the historical record of the week's liturgy and pew-insert print files.
5. `npm run build` locally to sanity-check, or push to `main` and let Netlify build.

## Adding a New Weekly Bulletin (Markdown / CMS Format)

1. From the live site, go to `/admin/`, log in with Netlify Identity, click "Bulletins" → "New Bulletin."
2. Fill in the fields (Date, Liturgical Day, Sermon Series, Call to Meditation, Call to Worship, Order of Worship items, Flowers, Announcements). The schema is `admin/config.yml`.
3. Publish — Decap commits a new `src/bulletins/<date>.md` to the `main` branch and Netlify rebuilds.
4. Alternatively, hand-edit a new `.md` file directly using `src/bulletins/2026-01-25.md` as the template, then commit and push.

## How the Homepage Picks the Latest

`src/index.njk` resolves the newest date across both formats: it takes the markdown collection's newest (sorted in `.eleventy.js`) and compares it against every entry in `scrollBulletins.json`, then either redirects to that URL via meta-refresh + JS replace or shows an empty state. `src/archive.njk` does the same union-and-sort to build the archive list.

## Source Working Folder: `bulletins/`

This is the non-published source folder — **not** `src/bulletins/`. Per-Sunday subdirectories typically contain:

- `<date> Liturgy.docx` / `Sermon Notes <date>.docx` — staff source documents.
- `bulletin-content-<date>` — plain-text extraction of the week's content (the same text that the scroll bulletin renders).
- `bulletin-<date>-booklet.html` / `.pdf` — generated print booklet (12in × 9in landscape spreads, used for the printed pew bulletin).
- Special-week inserts: lyrics inserts, graduates inserts, hands-to-hearts inserts, stage charts, practice charts, etc.
- `bulletin_example.pdf`, `Liturgy Notes.docx`, `pew insert.pdf`, `pew-insert-print.html/.pdf` at the top of `bulletins/` are reference artifacts kept for format/layout reference.

Keep this folder organized by date; it is the institutional record.

## Design Tokens

Both formats share one brand palette, taken from the church website (fpccorinth.org, rebuilt 2026): sage green as the primary color, a warm near-black for text, khaki neutrals for rules, an olive-gold accent, and Work Sans for labels/UI. Body copy on the bulletin stays in EB Garamond (a serif that echoes the logo wordmark) for long-form liturgical reading.

**Markdown bulletin (`bulletin.njk` + Tailwind theme in `tailwind.config.js`):**

| Token | Value | Usage |
|-------|-------|-------|
| sage | `#6d8c83` | Header/footer fills, primary buttons, hymn numbers |
| sage-deep | `#56736a` | Sage for small text (labels, links) — passes AA on light backgrounds |
| sage-light | `#eef2ef` | Sage-tinted surfaces, hover text on sage fills |
| ink / ink-muted | `#171200` / `#666558` | Body text / secondary text |
| gold | `#847539` | Olive-gold accent (sparingly) |
| khaki | `#b0af9b` | Rules, borders |
| cream / cream-dark | `#fdfdfb` / `#eceeea` | Page background / cards |
| Sans | Work Sans | Header, labels, UI |
| Serif | EB Garamond | Headings, body |

**Scroll bulletin (CSS variables inside each `src/scroll/*.html`, also `about.njk` / `archive.njk`):**

| Token | Value | Usage |
|-------|-------|-------|
| `--ink` | `#171200` | Body text |
| `--ink-light` | `#3f3d32` | Secondary text |
| `--ink-faint` | `#666558` | Tertiary text, notes |
| `--parchment` | `#eceeea` | Page background (outside the column) |
| `--warm-white` | `#fdfdfb` | Card / column background |
| `--section-bg` | `#eef2ef` | Sage-tinted section gradients |
| `--accent` | `#6d8c83` | Sage — fills, rules, hover backgrounds |
| `--accent-deep` | `#56736a` | Sage for small text (eyebrows, section headings, labels) |
| `--accent-soft` | `#8aa39b` | Hover state on sage fills |
| `--gold` / `--gold-soft` | `#847539` / `#ada37c` | Olive-gold accents (motto) |
| `--rule` / `--rule-light` | `#b0af9b` / `#d9d9cf` | Hairline rules |
| Display | Work Sans 300 | Cover date, welcome title, page title, sermon title |
| UI eyebrow | Work Sans 500–600, uppercase, tracked | Eyebrows, section headings, nav, labels |
| Body | EB Garamond | Body copy, italics, secondary titles |

When copying the previous week's scroll file, the palette comes along automatically. Never reintroduce the old navy/gold (`#1a3a5c`/`#c9a959`) or parchment/burgundy (`#f0ebe0`/`#8b1a1a`) values.

## Routes

```
/                  →  Redirect to the latest bulletin (across both formats)
/YYYY-MM-DD/       →  Markdown bulletin
/scroll/YYYY-MM-DD/  →  Scroll-format HTML bulletin
/archive/          →  Combined list of all bulletins, newest first
/about/            →  Church info + service times
/admin/            →  Decap CMS (requires Netlify Identity login)
```

## Netlify Build

`netlify.toml` runs `npm run build` and publishes `_site/`. NODE_VERSION is pinned to 20. Pushes to `main` trigger a deploy. Netlify Identity is required for CMS access.

## Conventions & Gotchas

- The `_site/` folder is build output; never edit by hand. The same applies to `src/css/style.css` (regenerated by Tailwind from `src/css/input.css`).
- The Eleventy `formatDate` filter accepts both `YYYY-MM-DD` strings and Date objects — it always pins to noon (`T12:00:00`) when given a string, to avoid the off-by-one-day timezone trap.
- Adding a scroll bulletin without adding it to `scrollBulletins.json` will publish the page but it will NOT appear in `/archive/` and the homepage redirect will not point at it.
- The dark-mode toggle is bootstrapped inline in `base.njk` before paint to avoid a flash. Don't move that script.
- `Be Blessed.mp3` is passthrough-copied from `src/` to `/audio/be-blessed.mp3` — link to that path, not the source filename.
- Service worker (`sw.js`) caches the shell — bump its cache version when shipping a CSS or JS change that needs to invalidate.
- Hymn numbers in `bulletin.njk` link to `https://hymnary.org/hymn/HWC1986/<number>` — that's Hymns for the Worship of God 1986 (HWC1986), the hymnal in use.

## Sample Reference Content

`src/bulletins/2026-01-25.md` is a fully-populated markdown bulletin and is the canonical example of every supported field. Reference it when authoring a new markdown bulletin or when extending the CMS schema in `admin/config.yml`.
