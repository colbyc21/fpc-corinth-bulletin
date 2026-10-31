"""Paths and environment for the builder.

Everything is derived from two environment variables so the same code runs
from a developer clone and from the deploy clone on basecamp:

* ``BUILDER_REPO`` - the git clone the app reads from and publishes.
  Defaults to the repo this file lives in.
* ``BUILDER_VAR``  - runtime data outside the repo (SQLite, proofs, uploads).
  Defaults to ``~/bulletin-builder/var``.

Secrets come from ``~/.config/bulletin-builder/env`` (mode 600) and the
Cloudflare API token from ``~/.config/cloudflare/env``. The systemd unit loads
both files; ``load_env()`` does the same for command-line use.
"""
from __future__ import annotations

import os
from pathlib import Path

ENV_FILE = Path(os.environ.get("BUILDER_ENV_FILE") or Path.home() / ".config/bulletin-builder/env")


def load_env(path: Path = ENV_FILE) -> None:
    """Populate os.environ from a KEY=VALUE file without overriding set vars."""
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


CLOUDFLARE_ENV_FILE = Path(os.environ.get("CLOUDFLARE_ENV_FILE") or Path.home() / ".config/cloudflare/env")

load_env()
load_env(CLOUDFLARE_ENV_FILE)

REPO = Path(os.environ.get("BUILDER_REPO") or Path(__file__).resolve().parents[1])
VAR = Path(os.environ.get("BUILDER_VAR") or Path.home() / "bulletin-builder/var")
BUILDER_DIR = REPO / "builder"
DATA_DIR = BUILDER_DIR / "data"
STANDING_DIR = DATA_DIR / "standing"
SEED_DIR = DATA_DIR / "seed"
TEMPLATES_DIR = BUILDER_DIR / "templates"
ASSETS_DIR = BUILDER_DIR / "assets"
SCHEMA_PATH = BUILDER_DIR / "pipeline/schema/week.schema.json"
BULLETINS_DIR = REPO / "bulletins"
SCROLL_DIR = REPO / "src/scroll"
SCROLL_INDEX = REPO / "src/_data/scrollBulletins.json"
CALENDAR_ICS = REPO / "src/calendar.ics"
CALENDAR_DATA = REPO / "src/_data/calendarEvents.json"
DB_PATH = VAR / "builder.sqlite"
PROOF_DIR = VAR / "proofs"

GIT_REMOTE_SSH = os.environ.get("BUILDER_GIT_REMOTE", "git@github.com:colbyc21/fpc-corinth-bulletin.git")
DEPLOY_KEY = Path(os.environ.get("BUILDER_DEPLOY_KEY") or Path.home() / ".ssh/fpc_bulletin_deploy")
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "http://127.0.0.1:8710")
SITE_URL = "https://bulletin.fpccorinth.org"
NETLIFY_DEPLOYS_URL = os.environ.get("NETLIFY_DEPLOYS_URL", "https://app.netlify.com/")
MODEL_CALLS = os.environ.get("BUILDER_MODEL_CALLS", "on").lower() not in {"off", "0", "false", "no"}
# Cloudflare Access application "FPC Bulletin Builder" on bulletin-admin.fpccorinth.org and its allow policy.
CF_ACCESS_APP_ID = os.environ.get("CF_ACCESS_APP_ID", "9ecb43fd-772a-476a-b2ee-2556e18e9ca2")
CF_ACCESS_POLICY_ID = os.environ.get("CF_ACCESS_POLICY_ID", "23b51a9b-d325-497e-94a1-b6cb4c7f9f7e")


def week_dir(date: str) -> Path:
    return BULLETINS_DIR / date


def ensure_dirs() -> None:
    VAR.mkdir(parents=True, exist_ok=True)
    PROOF_DIR.mkdir(parents=True, exist_ok=True)
