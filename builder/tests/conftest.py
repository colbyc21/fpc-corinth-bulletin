import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
os.environ.setdefault("BUILDER_REPO", str(REPO))

import pytest  # noqa: E402


@pytest.fixture(scope="session")
def tmp_var(tmp_path_factory):
    """A throwaway BUILDER_VAR with a seeded store, so tests never touch the real one."""
    var = tmp_path_factory.mktemp("var")
    from builder import config
    config.VAR = var
    config.DB_PATH = var / "builder.sqlite"
    config.PROOF_DIR = var / "proofs"
    from builder.pipeline.store import Store
    s = Store(config.DB_PATH)
    s.seed_if_empty()
    return var


@pytest.fixture(scope="session")
def store(tmp_var):
    from builder import config
    from builder.pipeline.store import Store
    return Store(config.DB_PATH)
