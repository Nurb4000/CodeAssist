"""Shared test fixtures and configuration."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import codeassist.session as _session_mod

# Redirect all DB operations to a test-only database so we never touch production data
_TEST_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "codeassist_test.db"


def _cleanup_db_files(db_path: Path):
    """Remove the main DB file and all WAL/SHM sidecar files."""
    for suffix in ("", "-shm", "-wal", "-journal"):
        f = db_path if suffix == "" else Path(str(db_path) + suffix)
        # A WAL/SHM sidecar may be checkpointed and removed between our
        # exists() check and unlink(); treat that as success.
        try:
            f.unlink(missing_ok=True)
        except FileNotFoundError:
            pass


@pytest.fixture(autouse=True)
def clean_database(monkeypatch):
    """Clean test database before each test to ensure isolation."""
    monkeypatch.setattr(_session_mod, "DB_PATH", _TEST_DB_PATH)
    _session_mod.reset_pool()
    _cleanup_db_files(_TEST_DB_PATH)
    yield
    _session_mod.reset_pool()
    _cleanup_db_files(_TEST_DB_PATH)


@pytest.fixture(autouse=True)
def _isolate_overrides_file():
    """Remove config.overrides.toml from cwd around each test.

    Settings edits write a sibling overrides file next to the loaded config.
    Tests run from the repo root, so scrub it before/after every test to keep
    UI edits from leaking across tests (and to never leave it in the tree).
    """
    overrides = Path.cwd() / "config.overrides.toml"
    try:
        overrides.unlink(missing_ok=True)
    except OSError:
        pass
    yield
    try:
        overrides.unlink(missing_ok=True)
    except OSError:
        pass


@pytest.fixture(autouse=True)
def _reset_settings_store():
    """SettingsStore caches state across tests; reset before each run."""
    from codeassist import settings as settings_mod

    settings_mod.settings_store._cache = {}
    settings_mod.settings_store._loaded = False
    yield
    settings_mod.settings_store._cache = {}
    settings_mod.settings_store._loaded = False


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
def live_client(monkeypatch, test_workspace):
    """Boot the real app against an isolated temp workspace and test database."""
    from codeassist import server

    _GLOBALS = (
        "_config", "tools", "skill_registry", "plugin_registry",
        "trust_registry", "lsp_client", "mcp_client",
    )
    saved = {name: getattr(server, name) for name in _GLOBALS}
    monkeypatch.setattr(server, "_config", None)
    cfg = server.get_config()
    monkeypatch.setattr(cfg.server, "workspace", str(test_workspace))
    cfg.workspace = test_workspace.resolve()
    try:
        with TestClient(server.app, raise_server_exceptions=False) as client:
            yield client
    finally:
        # Restore module globals the app lifespan mutated, to avoid leaking
        # server state into other tests (e.g. the live tool registry).
        for name, old in saved.items():
            setattr(server, name, old)


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
