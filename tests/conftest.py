"""Shared test fixtures and configuration."""
import pytest
import codeassist.session as _session_mod
from pathlib import Path


# Redirect all DB operations to a test-only database so we never touch production data
_TEST_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "codeassist_test.db"


def _cleanup_db_files(db_path: Path):
    """Remove the main DB file and all WAL/SHM sidecar files."""
    for suffix in ("", "-shm", "-wal", "-journal"):
        f = db_path if suffix == "" else Path(str(db_path) + suffix)
        if f.exists():
            f.unlink()


@pytest.fixture(autouse=True)
def clean_database(monkeypatch):
    """Clean test database before each test to ensure isolation."""
    monkeypatch.setattr(_session_mod, "DB_PATH", _TEST_DB_PATH)
    _session_mod.reset_pool()
    _cleanup_db_files(_TEST_DB_PATH)
    yield
    _session_mod.reset_pool()
    _cleanup_db_files(_TEST_DB_PATH)


@pytest.fixture
async def initialized_db():
    """Initialize the database for testing."""
    await _session_mod.init_db()
    return _session_mod.DB_PATH


@pytest.fixture
def test_workspace(tmp_path):
    """Create a temporary workspace for testing."""
    return tmp_path


@pytest.fixture
def sample_skill_content():
    """Return sample skill markdown content."""
    return """---
name: test-skill
description: A test skill for unit tests
slash: test
---

This is the skill content.
It should be parsed correctly.
"""


@pytest.fixture
def sample_session_messages():
    """Return sample messages for session testing."""
    return [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi there!"},
        {"role": "user", "content": "How are you?"},
        {"role": "assistant", "content": "I'm doing well, thanks!"},
    ]
