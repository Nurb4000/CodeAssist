"""Shared test fixtures and configuration."""
import asyncio
import pytest
import session as _session_mod
from pathlib import Path


# Redirect all DB operations to a test-only database so we never touch production data
_TEST_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "codeassist_test.db"


@pytest.fixture(autouse=True)
def clean_database(monkeypatch):
    """Clean test database before each test to ensure isolation."""
    # Point the module-level DB_PATH to our test database
    monkeypatch.setattr(_session_mod, "DB_PATH", _TEST_DB_PATH)
    # Also patch the pool so it uses the test path
    _session_mod.reset_pool()
    if _TEST_DB_PATH.exists():
        _TEST_DB_PATH.unlink()
    yield
    _session_mod.reset_pool()
    if _TEST_DB_PATH.exists():
        _TEST_DB_PATH.unlink()


@pytest.fixture
def event_loop():
    """Create an event loop for async tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


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
