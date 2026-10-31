"""Paths and environment for the builder.

Everything is derived from two environment variables so the same code runs
from a developer clone and from the deploy clone on basecamp:

* ``BUILDER_REPO`` - the git clone the app reads from and publishes.
  Defaults to the repo this file lives in.
* ``BUILDER_VAR``  - runtime data outside the repo (SQLite, proofs, uploads).
  Defaults to ``~/bulletin-builder/var``.

Secrets come from ``~/.config/bulletin-builder/env`` (mode 600). The systemd
unit loads that file; ``load_env()`` does the same for command-line use.
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


load_env()

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

# Model steps (§3.1, §8). One model for all three call kinds; effort per kind.
MODEL = os.environ.get("BUILDER_MODEL", "claude-opus-5-5")
MONTHLY_CAP_USD = float(os.environ.get("BUILDER_MONTHLY_CAP_USD", "40"))
WARN_FRACTION = 0.8
JOB_CAPS = {  # kind: (max calls, max USD)
    "parse": (2, 1.50),
    "content": (2, 1.00),
    "fit": (8, 3.00),
    "chat": (4, 1.00),
}
BUILDS_PER_WEEK = 3
PROMPTS_DIR = BUILDER_DIR / "prompts"
KNOWLEDGE_DIR = REPO / "docs/knowledge"


def week_dir(date: str) -> Path:
    return BULLETINS_DIR / date


def ensure_dirs() -> None:
    VAR.mkdir(parents=True, exist_ok=True)
    PROOF_DIR.mkdir(parents=True, exist_ok=True)
