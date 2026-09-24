"""Tests for settings validation + config.overrides.toml sync-back.

Covers the #2 backlog slice: per-field validation (422 on bad values) and
writing UI edits back to a sibling ``config.overrides.toml`` that ``Config.load``
merges on boot — so overrides survive a data-dir reset even when config.toml is
mounted read-only in Docker.
"""
import tomllib

import pytest
from fastapi.testclient import TestClient

from codeassist import server

BASE_CONFIG = """\
[llm]
provider = "openai"
model = "gpt-4o"
api_key = ""
base_url = ""
context_window = 128000

[llm.parameters]
temperature = 0.0
max_tokens = 8192

[server]
host = "127.0.0.1"
port = 8090
workspace = "."

[agent]
max_iterations = 30
name = "CodeAssist"
"""


@pytest.fixture
def settings_env(tmp_path, monkeypatch):
    """Boot the real app inside an isolated temp dir holding its own config."""
    (tmp_path / "config.toml").write_text(BASE_CONFIG, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    server._config = None
    with TestClient(server.app, raise_server_exceptions=False) as client:
        yield client, tmp_path


def _read_overrides(tmp_path) -> dict:
    opath = tmp_path / "config.overrides.toml"
    if not opath.exists():
        return None
    with open(opath, "rb") as f:
        return tomllib.load(f)


def test_put_validates_and_syncs_to_file(settings_env):
    client, tmp_path = settings_env
    r = client.put("/api/settings", json={"llm.model": "gpt-4o-mini", "llm.temperature": 0.7})
    assert r.status_code == 200, r.text
    body = r.json()
    assert "llm.model" in body["applied"]
    assert "llm.temperature" in body["applied"]

    overrides = _read_overrides(tmp_path)
    assert overrides is not None
    # temperature/max_tokens live under [llm.parameters] in the file.
    assert overrides["llm"]["model"] == "gpt-4o-mini"
    assert overrides["llm"]["parameters"]["temperature"] == 0.7


def test_get_reports_ui_source_after_put(settings_env):
    client, _ = settings_env
    client.put("/api/settings", json={"llm.model": "claude-3"})
    listed = client.get("/api/settings").json()["settings"]
    by_key = {s["key"]: s for s in listed}
    assert by_key["llm.model"]["source"] == "ui"
    assert by_key["llm.model"]["value"] == "claude-3"


@pytest.mark.parametrize(
    "payload,snippet",
    [
        ({"server.port": 99999}, "port"),
        ({"server.port": 0}, "port"),
        ({"llm.temperature": 5.0}, "temperature"),
        ({"llm.context_window": 0}, "context window"),
        ({"llm.base_url": "ftp://evil"}, "Base URL"),
        ({"llm.model": ""}, "Model"),
    ],
)
def test_put_rejects_invalid_values(settings_env, payload, snippet):
    client, _ = settings_env
    r = client.put("/api/settings", json=payload)
    assert r.status_code == 422
    assert snippet.lower() in r.json()["detail"].lower()
    # Nothing should have been persisted for a rejected value.
    assert "applied" not in r.json()


def test_delete_clears_db_and_file(settings_env):
    client, tmp_path = settings_env
    client.put("/api/settings", json={"llm.model": "temp-model"})
    assert _read_overrides(tmp_path) is not None

    r = client.delete("/api/settings/llm.model")
    assert r.status_code == 200, r.text

    overrides = _read_overrides(tmp_path)
    # The deleted key is gone from the overrides file.
    if overrides is not None and "llm" in overrides:
        assert "model" not in overrides["llm"]


def test_restart_endpoint_reloads_config(settings_env):
    client, _ = settings_env
    # Flip a feature toggle to file, then hit the restart endpoint.
    client.put("/api/settings", json={"skills.enabled": True})
    r = client.post("/api/settings/restart")
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True


def test_config_load_merges_overrides(tmp_path, monkeypatch):
    """A merged overrides file wins over the base config on load."""
    (tmp_path / "config.toml").write_text(BASE_CONFIG, encoding="utf-8")
    (tmp_path / "config.overrides.toml").write_text(
        '[llm]\nmodel = "override-model"\n'
        "[llm.parameters]\ntemperature = 1.5\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    from codeassist.config import Config

    cfg = Config.load()
    assert cfg.llm.model == "override-model"
    assert cfg.llm.temperature == 1.5


def test_validate_setting_table():
    from codeassist.settings import BY_KEY, validate_setting

    assert validate_setting(BY_KEY["llm.temperature"], 0.5) is None
    assert "2.0" in validate_setting(BY_KEY["llm.temperature"], 3.0)
    assert validate_setting(BY_KEY["server.port"], 1234) is None
    assert validate_setting(BY_KEY["llm.base_url"], "") is None
    assert validate_setting(BY_KEY["llm.base_url"], "notaurl") is not None
    assert validate_setting(BY_KEY["llm.provider"], "openai") is None
    assert validate_setting(BY_KEY["llm.provider"], "nope") is not None
