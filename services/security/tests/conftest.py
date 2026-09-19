import os

import pytest


@pytest.fixture(autouse=True)
def isolate_from_outer_git(monkeypatch):
    """Git exports GIT_INDEX_FILE, GIT_DIR and friends to hooks. Without this,
    the temp repos these tests create would write into the index of the
    commit that triggered the pre-commit hook."""
    for name in [n for n in os.environ if n.startswith("GIT_")]:
        monkeypatch.delenv(name)
