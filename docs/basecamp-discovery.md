# Basecamp Discovery — Phase 0 (§11)

Run on basecamp by Claude Code, 2026-10-29, from the clone at `~/fpc-corinth-bulletin` (on `main` at `4822bd2`, in sync with `origin/main`). Each finding lists the command used and what it returned. Anything that differs from §11.0 of the plan is marked **Changed**.

## Summary

- The box is healthy and has plenty of headroom. The pending updates and reboot from 10-29 have been applied (it is now on Ubuntu 24.04.**5**, 0 updates pending).
- **The print booklet renders on this box with the same page size and layout as the Mac.** Same text, 0 overflow on all 8 panels, about 1 % pixel difference. Times New Roman is replaced by Liberation Serif, which has the same character widths. The one real difference is a **missing giving QR code**: `qrcode.png` (and `logo.png`) at the repo root were never committed.
- Eleventy builds cleanly in 1.4 s. A test push to GitHub worked.
- **`sudo` asks for a password** (it is not passwordless). This affects anything that writes to `/etc` or uses apt. See "Needs Colby at a terminal" below.
- No reverse proxy is installed (no nginx or Caddy) and none is needed. Exposure goes through Tailscale Serve or the existing host `cloudflared` tunnel, the same way the sermons app is exposed.
- No calendar or roster system (Google Calendar, Planning Center, Breeze) turned up on the box or in the repo. Seed data has been extracted into `builder/data/` (see §11.1 "Existing data to seed").

## Needs Colby at a terminal (password sudo), when we get there

These do not block Phase 1. Each one is a one-line command:

1. **Cloudflare ingress.** `/etc/cloudflared/config.yml` is owned by root. Adding a `bulletin-admin.fpccorinth.org` hostname means `sudo` editing that file, then `sudo systemctl restart cloudflared`. The DNS route itself does not need sudo, because `~/.cloudflared/cert.pem` exists.
2. **apt packages.** Only optional ones: `poppler-utils` (for `pdftoppm`) and `unzip`. Workarounds are in place: PyMuPDF rasterizes the PDFs and Python's `zipfile` reads the docx files. **No apt package is required.**
3. **Secrets file.** The plan's `/etc/bulletin-builder.env` would need sudo. Proposed instead: `~/.config/bulletin-builder/env` with mode 600, matching the sermons app's user-level `.env` pattern. With this change, nothing in the app needs root.

---

## 11.1 Inventory

### OS and resources

```
$ lsb_release -d; uname -r; nproc; free -h; df -h /; nvidia-smi --query-gpu=name,driver_version,memory.used --format=csv
Ubuntu 24.04.5 LTS          # Changed: was 24.04.4; updates + reboot applied (uptime 32 min at check)
6.8.0-147-generic
12  (AMD Ryzen 5 5600 6-Core)
Mem: 62Gi total, 59Gi available; Swap 8Gi
/dev/nvme0n1p2  915G  121G  748G  14% /
NVIDIA GeForce RTX 3090, 580.178.04, 1 MiB used of 24576 MiB
$ /usr/lib/update-notifier/apt-check --human-readable   → 0 updates can be applied immediately
```

The app does not need the GPU. Ollama (11434) and Open WebUI use it; leave them alone.

### What already runs

`ss -tlnp` (without sudo, so owners show only for clovis's own processes):

| Port / bind | Owner |
|---|---|
| 0.0.0.0:3001 | `node` — fpc-sermons (user unit) |
| *:3000, *:8091 | zwave-js-ui (container) |
| 0.0.0.0:8123 | Home Assistant |
| 0.0.0.0:1883 | Mosquitto |
| 0.0.0.0:8080, 0.0.0.0:8989 | (other containers / Open WebUI) |
| 100.105.57.81:80, 127.0.0.1:8090 | lifeos-frontend |
| 127.0.0.1:5432 | lifeos-db (pgvector pg16) |
| 127.0.0.1:18789/18791/18792, [::1]:18789 | openclaw-gateway (user unit) |
| *:11434 | ollama.service |
| tailscale0 :443/:8443/:8444 | Tailscale Serve |
| 127.0.0.1:20241, 127.0.0.1:18554, *:18555, 192.168.6.21:1400/36725 | cloudflared metrics / HA integrations |
| **8710** | **free** (`ss -tln | grep -c ':8710 '` → 0) |

- System services (`systemctl list-units --type=service --state=running`): cloudflared, docker, containerd, ollama, tailscaled, ssh, nvidia-persistenced, unattended-upgrades, plus the usual base services.
- User services (`systemctl --user …`): `basecamp`, `fpc-sermons`, `openclaw-gateway`, `trinity`. These match the plan.
- Containers (`docker ps`): lifeos-frontend, lifeos-backend, lifeos-db, lifeos-cloudflared, homeassistant, zwave-js-ui, mosquitto, open-webui (healthy). The stale `fpc_sermons-app-1` container no longer appears in `docker ps`.

### Reverse proxy and exposure

```
$ which nginx caddy traefik        → none
$ ls /etc/letsencrypt              → absent
$ tailscale serve status
https://basecamp.tailfe9d52.ts.net        / → http://127.0.0.1:18789   (openclaw)
https://basecamp.tailfe9d52.ts.net:8443   / → http://localhost:8123     (Home Assistant)
https://basecamp.tailfe9d52.ts.net:8444   / → http://127.0.0.1:8090     (lifeos)
$ cat /etc/cloudflared/config.yml   (root:root 644; ExecStart … --config /etc/cloudflared/config.yml tunnel run)
ingress:
  - hostname: sermons.fpccorinth.org → http://localhost:3001
  - service: http_status:404
$ ls ~/.cloudflared   → cert.pem, config.yml (same ingress), <tunnel-id>.json
$ docker inspect lifeos-cloudflared-1 → token-based tunnel (TUNNEL_TOKEN env), a separate tunnel for lifeos
```

This box has two tunnels. The **host** tunnel serves fpccorinth.org hostnames. The `lifeos` container runs its own token-managed tunnel. The bulletin app belongs on the host tunnel. The `fpccorinth.org` zone is on Cloudflare (NS `savanna`/`drew.ns.cloudflare.com`), and `bulletin.fpccorinth.org` (Netlify) is already proxied through Cloudflare. So a new hostname plus Cloudflare Access are both available.

`sudo -n ufw status` needs a password, so the firewall state could not be read. With Tailscale Serve or the tunnel, the app binds to 127.0.0.1 and needs no ufw change.

### Existing sermons app (the auth to reuse)

`~/fpc_sermons`: Fastify 5 + TypeScript, SQLite, EJS. It runs as a user unit on :3001 and is exposed through the host tunnel.

- **Auth:** passwordless magic link. `POST /login` looks the email up in an `admins` table, stores a 32-byte token in `magic_links`, and sends the link through **Resend** (`RESEND_API_KEY`, from `noreply@fpccorinth.org`). Login is rate-limited to 5 per 15 minutes and each attempt is written to `login_audit`. Sessions use `@fastify/secure-session`.
- It has **6 active admins: 1 `@fpccorinth.org`, 4 `@gmail.com`, 1 `@proton.me`** (`select … from admins where deleted_at is null`).
- **The session itself cannot be shared.** The two apps would live on different hostnames, and secure-session cookies are libsodium-encrypted in a Node-specific format that a Python app cannot read without reimplementing it. **The pattern and the Resend key can be reused**: build the same magic-link flow in the bulletin app, with its own `users` table and roles.
- The sermons `.env` has a **non-empty `ANTHROPIC_API_KEY`**, used by its auto-process pipeline. The bulletin app must not borrow it (§8 calls for a dedicated workspace).

### Network reach

```
$ tailscale status          → basecamp 100.105.57.81; colbysmba active (direct, LAN 192.168.4.45); others offline 35–194 d
$ tailscale status --json   → Self.KeyExpiry = None   # node key expiry is now disabled ✓
$ dig +short MX fpccorinth.org → ASPMX.L.GOOGLE.COM   # church email is Google Workspace
```

The box is reachable on the LAN (192.168.6.21) and the tailnet, and from the internet **only** through the Cloudflare tunnel's explicit hostnames. Today the tailnet has only Colby's devices, so tailnet-only exposure would mean adding each volunteer to Tailscale.

### Toolchain

```
python3 3.12.3 · venv + ensurepip OK
node v22.23.4 · npm 10.9.9         # Changed: Node was 22.22
git 2.43.0 · Docker 29.9.2 · Claude Code 2.1.312   # Changed: was 2.1.66
gh — logged in as colbyc21 (scopes: gist, read:org, repo, workflow); git uses gh as credential helper for github.com
missing: unzip, pdftoppm/pdffonts (poppler-utils), unrtf, sqlite3 CLI, chromium (apt), textutil (macOS-only, expected)
sudo -n true → "a password is required"
```

Fonts (`fc-list`, `dpkg -l 'fonts-*'`): fonts-liberation, dejavu, freefont, noto-color-emoji, and a few CJK packages. No Times New Roman, Georgia, EB Garamond or Work Sans. `fc-match "Times New Roman"` returns Liberation Serif, and `fc-match Georgia` returns DejaVu Serif.

Python packages installed in a scratch venv without trouble: `playwright 1.64.1`, `pypdf`, `pymupdf`, `striprtf`, `python-docx`. `playwright install chromium` (headless shell 156.0.8078.4) took 9 s, and `ldd` showed **no missing shared libraries**, so `--with-deps` (and therefore sudo) is not needed.

Note: running the Chromium binary directly with today's CLI flags fails with `No usable sandbox!`, because Ubuntu 24.04 sets `kernel.apparmor_restrict_unprivileged_userns=1`. Playwright's API launches it without the sandbox and works. **Render through the Playwright API, not the raw CLI** (or add `--no-sandbox`).

### Rendering check (acceptance item 1)

The renderer used Playwright `page.pdf(prefer_css_page_size=True, print_background=True)` on `bulletins/2026-11-01/bulletin-2026-11-01-legal.html`. The script is saved as `builder/scripts/phase0_render_check.py`.

| | Committed (Mac Chrome, Skia/PDF m156) | basecamp (Playwright headless shell m156) |
|---|---|---|
| Pages | 4 (2 sheets duplex = 8 panels) | 4 |
| MediaBox | `[0 0 1008 612]` | `[0 0 1008 612]` ✓ |
| Fonts | TimesNewRomanPSMT, -BoldMT, -ItalicMT, -BoldItalicMT | LiberationSerif, -Bold, -Italic, -BoldItalic |
| Text per page | — | identical on pages 2–4. Page 1 has one extra line: "QR Code" |
| Pixel diff (60 dpi, >40 levels) | — | 1.1 %, 0.8 %, 0.8 %, 1.8 % |
| Panel overflow (`scrollHeight - clientHeight`) | — | 0 on all 8 panels |
| Render time | — | 1.2 s |

- **Fonts.** The print template uses `font-family: "Times New Roman", "Georgia", serif`. It does **not** use EB Garamond or Work Sans; those are only on the scroll site, which loads them from Google Fonts in the browser. Liberation Serif has the same character widths as Times New Roman, so line breaks and page fit came out the same. **Proposal:** in the Phase 1 template, name `"Liberation Serif"` explicitly as the second font in the stack, so the result does not depend on fontconfig. Bundling Times New Roman is not an option because of its license.
- **The "QR Code" line** is the `alt` text of `<img src="../../qrcode.png">`. That file is missing (see below).

### Missing assets (found during the render check)

`.eleventy.js` passthrough-copies `logo.png` and `qrcode.png` from the repo root, and every print booklet references `../../qrcode.png`. Neither file is in git; they exist only in Colby's Drive folder. As a result:

```
$ curl -o /dev/null -w '%{http_code}' https://bulletin.fpccorinth.org/images/qrcode.png   → 404
$ curl … /images/logo.png → 404 ;  /images/fpc-logo-2026.png → 200
```

The live site is missing both files too. Fix in Phase 1: commit `qrcode.png` to the repo, either from Colby's Mac or by extracting it from the image embedded in a committed PDF. Check which giving URL it encodes before committing it.

### Eleventy build

```
$ npm ci --no-audit --no-fund   → 1.3 s
$ npm run build                 → "Copied 49 Wrote 45 files in 0.22 seconds (v3.1.2)", 1.4 s wall, no warnings
$ git status --short            →  M src/css/style.css     (Tailwind rewrite, as documented)
$ git checkout -- src/css/style.css
```

`node_modules` is a normal directory here, not the Mac's symlink, and it is gitignored. `netlify.toml` builds `npm run build` into `_site` on Node 20.

### GitHub access (acceptance item 2)

```
$ git worktree add …/wt -b basecamp-test main && git commit --allow-empty -m "basecamp Phase 0 push test …"
$ git push origin basecamp-test     → OK (230c9dd)
$ git ls-remote --heads origin      → basecamp-test 230c9dd…, main 4822bd2…
$ git push origin --delete basecamp-test → OK; only main remains
```

Pushes work today through **Colby's personal `gh` OAuth token**, which has full `repo` scope on every repo he owns. The app should not use it. A repo-scoped deploy key was generated for the app:

```
$ ssh-keygen -t ed25519 -N '' -C bulletin-builder@basecamp -f ~/.ssh/fpc_bulletin_deploy
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIPDw5eedklcWD/KpyItLl4jF2TkRrC1/7d+Zx0ZhvngE bulletin-builder@basecamp
```

Netlify may have started a branch deploy for `basecamp-test` during the few seconds the branch existed. That is harmless.

### Netlify

`netlify.toml`: `command = "npm run build"`, `publish = "_site"`, `NODE_VERSION = "20"`. The live site answers through Cloudflare with an `x-nf-request-id` header, i.e. Netlify behind Cloudflare. No Netlify token or CLI config exists on the box (`~/.config/netlify` absent). `/calendar.ics` returns 404 today, as expected.

### Calendar and roster sources

```
$ grep -rIilE 'planningcenter|breeze|calendar\.google|\.ics\b|churchcenter|realm|elexio' (repo, sermons src) → no integrations
$ find ~ -maxdepth 4 -iname '*.ics' -o -iname '*roster*' -o -iname '*volunteer*' -o -iname '*serve*schedule*' → nothing
$ find / -maxdepth 3 -iname '*drive*' → no Drive mirror on the server
```

Nothing to import from. Church email is on Google Workspace, so a church Google Calendar may exist without appearing here (§11.3 item 6).

### Existing data to seed → `builder/data/`

Script: `builder/scripts/phase0_seed.py` (`python -I phase0_seed.py <repo> builder/data`).

**Source choice (deviates from the plan).** I extracted from the **published scroll pages** (`src/scroll/<date>.html`) rather than the raw `bulletin-content-*.rtf` files. The published pages hold the corrected, final text; the RTFs hold the uncorrected submissions. Two examples from 11-01:
- The RTF says "Assisting Elder - **Dr. Don Shipp**"; the published page says **Dr. John Shipp**.
- The RTF's Children's Church paragraph is the *school-break* wording; the published page uses the *regular* wording.

The RTFs are still useful later as before/after training pairs for the copyedit prompt.

| File | Contents |
|---|---|
| `seed/rosters.csv` | 22 Sundays, 2026-06-07 → 11-01. Columns: assisting_elder, ushers, greeters, nursery, acolytes, av_engineer, circle_of_prayer, childrens_church |
| `seed/events.csv` | 74 distinct calendar lines (date, text, which bulletins listed it). Multi-day events keep their range as a `(Sept. 26 – Oct. 3)` prefix; "Today" resolves to that Sunday |
| `standing/staff.json` | 10 staff (Rev. Waring Porter … Sammy Allred) from 11-01 |
| `standing/welcome.json` | welcome text; Children & Nursery `regular` (11-01) and `school_break` (10-11); vision & mission |
| `standing/pew-bible-pages.json` | 16 references, pp. 390–398 (Ezra 3 → Nehemiah 1), 945 (Acts 9), 967. Far more than the plan's three |
| `standing/hymns.json` | 52 hymn numbers from scroll pages since 2026-02-15 plus liturgy docx files since 06-28. 8 numbers have title variants, which are kept (e.g. #76 "O For a 1000 Tongues" in the docx vs "Thousand" as published) |

- **Name spellings.** I applied `docs/knowledge/name-spellings.md`. It caught 4 errors in *published* bulletins: Malachi Doran (06-21), Malachi Doren (08-23), and Dr. Stephen Blair (09-27). The plan's acceptance test exists for good reason.
- **New inconsistency, not in the note:** "Jason & Jennifer **LeGoff**" (2 weeks) vs "**Legoff**" (11-01). Confirmed **LeGoff** (§11.3 below); note and seed updated.
- **Liturgy docx history starts at 2026-06-28.** The plan's "since 2026-02-15" range exists only as published scroll pages. Before that there are scroll pages but no week folders; 07-05 and 07-12 have scroll pages but no folder.
- Some folders reuse an older content file name (08-02/08-09/08-16 contain `bulletin-content-2026-07-26.rtf`). Look up the content file by glob, not by date.

### Anthropic access

```
$ env | grep -i anthropic   → none in the shell
$ ls ~/.config/ant          → absent;  `ant` CLI not installed
```

The only key on the box belongs to the sermons app (above). No key was created.

---

## 11.2 Decisions (proposed defaults — accept with a yes)

1. **Hostname and path.** Run the app on `127.0.0.1:8710`. Proposed exposure: **`bulletin-admin.fpccorinth.org` on the host cloudflared tunnel, with Cloudflare Access in front**, matching the sermons app.
   - The pastor and the office administrator are not on the tailnet, and a hostname works from any office PC.
   - Tailscale Serve on `https://basecamp.tailfe9d52.ts.net:8445` is available as a fallback if this should stay Colby-only for now.
   - The app's paths sit at the root of its own hostname (`/`, `/weeks/<date>`, …), not under `/bulletin/`.
2. **Login.** The box *is* internet-reachable through the tunnel, so the plan's default would be Google sign-in limited to `@fpccorinth.org`. **However, 5 of the 6 people who log into the sermons app use gmail or proton addresses.** Proposal: Cloudflare Access (an email one-time PIN, with an allowlist of exact addresses) as the outer gate, plus the **sermons-app magic-link pattern through the same Resend account** inside the app for identity and roles. This works with any email address, needs no passwords to manage, and has a proven audit trail.
3. **Locations and user.**
   - App code goes **in this repo under `builder/`**: one history, and the Phase 0 seeds are already there. Netlify ignores it, because Eleventy reads only `src/` and the passthroughs.
   - The running service uses a **separate deploy clone** at `~/bulletin-builder/repo` (pushes over SSH with the deploy key). Runtime data (SQLite, uploads in progress, renders) goes in `~/bulletin-builder/var/`, and secrets in `~/.config/bulletin-builder/env` (mode 600).
   - The service runs as **user `clovis` under a systemd *user* unit**, the same as fpc-sermons. The plan's separate system user and `/etc` env file would both need sudo, and the sermons app already established the user-unit pattern. Linger is already enabled.
4. **Share the sermons app's auth?** The session cannot be shared (different hostname and language). The Resend key and the magic-link design are reused.
5. **Fonts.** Nothing needs installing. The print template uses Times New Roman, and on this box fontconfig substitutes Liberation Serif, which is already installed, has the same character widths, and fit with zero overflow. Phase 1 will name Liberation Serif explicitly in the stack. EB Garamond and Work Sans load from Google Fonts on the scroll site only. The kids bulletin's web fonts still need `--virtual-time-budget`, or a Playwright wait for `document.fonts.ready`, in Phase 4.
6. **Calendar or roster source.** None found. Default: the app owns the events and roster tables, seeded from `builder/data/seed/`.

Also, rendering goes through Playwright's API (with `pymupdf` for PNG proofs), not the Chromium CLI or `pdftoppm`, because of the AppArmor sandbox restriction and the missing poppler-utils.

---

## 11.3 Answers from Colby

Asked and answered 2026-10-29. "Default" means the §11.3 default in the plan, as adjusted by §11.2 above.

| # | Question | Answer |
|---|---|---|
| 1 | Users | **Default.** Office administrator as editor; the pastor and Colby as publishers; Colby as admin. The pastor keeps writing Liturgy and Sermon Notes in Word. |
| 2 | Login | **Cloudflare Access + magic link** (the §11.2 proposal). `bulletin-admin.fpccorinth.org` on the host cloudflared tunnel; Cloudflare Access with an email one-time PIN and an exact-address allowlist as the outer gate; the sermons-app magic-link pattern through the same Resend account inside the app for identity and roles. |
| 3 | Publish rights | **Default.** Editors mark *Ready*; only publishers press Publish. |
| 4 | GitHub | **Push to `main`.** Colby adds the deploy key above (`~/.ssh/fpc_bulletin_deploy.pub`) to the repo with write access. The server never uses Colby's personal `gh` token. |
| 5 | Budget | **Default.** Colby creates the `bulletin-builder` Console workspace with a $40 monthly limit and email alerts. Key goes in `~/.config/bulletin-builder/env` (mode 600), not `/etc`. Ask for the key only once the workspace exists. |
| 6 | Calendar source | **App owns the events table.** The church has Google Workspace but probably no main calendar. Decision: run the calendar in the app for now, seeded from `builder/data/seed/events.csv`, and publish `calendar.ics`. Moving the source of truth to a Google Calendar later stays an option; the ICS feed makes that a one-way export if wanted. |
| 7 | Rosters | **Seed only, enter weekly.** What exists is a schedule for the whole year that changes ad hoc when people are away. No import in Phase 1. The roster screen starts from the 22 seeded Sundays and each week is confirmed or edited by hand. A yearly-schedule import is a candidate for a later phase. |
| 8 | Pew Bible and hymnal | **Pew Bible: ESV Thinline.** **Hymnal: *The Hymnal for Worship & Celebration*** (brown, Word Music 1986, Fettke and Barker, ISBN 9783010026369), confirmed by Colby 2026-10-31. All 52 seeded numbers matched that hymnal's index; the full table was then filled from hymnary.org/hymnal/HWC1986. One transposition surfaced: the liturgy (and the printed bulletins of 2026-06-14 and 2026-10-04) cited #345 for *Leaning on the Everlasting Arms*, which is #354; #345 is *Blessed Assurance*. The table records both and the parse step should flag a number/title mismatch like this. |
| 9 | Google Drive mirror | **No.** The git repo is the record; Colby's laptop clone still syncs to Drive. |
| 10 | Printing | **Download button only.** |
| 11 | Kids bulletin | **Phase 4.** |
| 12 | Netlify polling | **Skip.** Show the commit hash and a link to the Netlify deploys page. |

Also confirmed: the family name is **LeGoff** (capital G); the 2026-11-01 bulletin's "Legoff" was wrong. Added to `docs/knowledge/name-spellings.md` and the seed script's spelling table.

**Waiting on Colby (not blocking Phase 1):** add the deploy key to GitHub; create the Console workspace and hand over the key; confirm the hymnal edition. *(All three done by 2026-10-31.)*



---

## Phase 1 notes (2026-10-29)

What changed on the box, so the next session does not rediscover it:

- **Deploy key works.** Added to the repo by Colby; `git ls-remote` and a push over `~/.ssh/fpc_bulletin_deploy` both succeeded. The deploy clone is `~/bulletin-builder/repo` with `core.sshCommand` set to that key and git identity `FPC Bulletin Builder <bulletin-builder@fpccorinth.org>`.
- **Runtime venv** at `~/bulletin-builder/venv` (`builder/requirements.txt`); Playwright Chromium headless shell 1248 installed. **Store** at `~/bulletin-builder/var/builder.sqlite`, seeded with 22 roster rows and 93 events (the four weekly items plus the one-off history; duplicates of weekly items were dropped and events the office listed more than a week ahead are marked *featured*).
- **Secrets file** `~/.config/bulletin-builder/env` (mode 600) exists with `SESSION_SECRET` generated and placeholders for `ANTHROPIC_API_KEY` and `RESEND_API_KEY`. Claude Code's permission classifier refused to copy the Resend key out of the sermons app's `.env`, so Colby does that by hand (one line, below). Without the key the app still works: the sign-in link is written to the journal instead of emailed.
- **qrcode.png** was recovered from the image embedded in the committed 2026-11-01 PDF (410×410, decodes to `https://onrealm.org/fpccorinth/-/form/give/now`) and committed at the repo root, so `/images/qrcode.png` and the print booklet's QR code work again.
- **Calendar feed.** `.eleventy.js` now passes `src/calendar.ics` through; `src/calendar.njk` renders `/calendar/` from `src/_data/calendarEvents.json`. Both files are regenerated on every publish.
- **Service.** `builder/deploy/bulletin-builder.service` is installed as a user unit, listening on `127.0.0.1:8710`. Not yet reachable from outside: the `bulletin-admin.fpccorinth.org` ingress on the host cloudflared tunnel and the Cloudflare Access policy are still the sudo step for Colby.

### Colby's remaining one-liners

```
# 1. Resend key (same account as the sermons app)
grep '^RESEND_API_KEY=' ~/fpc_sermons/.env >> ~/.config/bulletin-builder/env   # then delete the empty placeholder line
# 2. Anthropic key for the bulletin-builder workspace
sed -i 's|^ANTHROPIC_API_KEY=.*|ANTHROPIC_API_KEY=sk-ant-...|' ~/.config/bulletin-builder/env
systemctl --user restart bulletin-builder
# 3. Cloudflare ingress (sudo): add before the http_status:404 line in /etc/cloudflared/config.yml
#      - hostname: bulletin-admin.fpccorinth.org
#        service: http://localhost:8710
#    then: cloudflared tunnel route dns <tunnel-name> bulletin-admin.fpccorinth.org && sudo systemctl restart cloudflared
#    and add a Cloudflare Access application for that hostname (email OTP, exact-address allowlist).
```

Item 8 (hymnal edition) was confirmed on 2026-10-31; nothing from §11.3 remains open.
