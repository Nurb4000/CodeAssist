"""Tests for disk-registry-backed edit/remove of skills, plugins, and custom
tools (backlog item #3). Covers the registry mutation helpers (path-scoped,
traversal-guarded) and the REST routes that write back to disk + reload.
"""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import codeassist.server as server


# --------------------------------------------------------------------------- #
# Registry-level unit tests (no app boot)                                    #
# --------------------------------------------------------------------------- #


def _write_skill(ws: Path, name: str, description: str, body: str, slash: str) -> Path:
    from codeassist.skills import SkillRegistry

    skill_dir = ws / "codeassist" / "skills"
    skill_dir.mkdir(parents=True, exist_ok=True)
    p = skill_dir / f"{name}.md"
    p.write_text(SkillRegistry.format_skill_file(name, description, body, slash), encoding="utf-8")
    return p


def test_skill_format_roundtrip(tmp_path):
    from codeassist.skills import SkillRegistry
    from codeassist.config import SkillsConfig

    cfg = SkillsConfig(enabled=True, directories=["codeassist/skills"])
    reg = SkillRegistry(tmp_path, cfg)
    fp = _write_skill(tmp_path, "demo", "A demo skill", "Body text", "demo")
    assert fp.exists()

    reg.discover()
    s = reg.get_skill("demo")
    assert s is not None
    assert s.name == "demo"
    assert s.description == "A demo skill"
    assert s.slash_command == "demo"
    assert s.content == "Body text"


def test_update_skill_rewrites_file(tmp_path):
    from codeassist.skills import SkillRegistry
    from codeassist.config import SkillsConfig

    cfg = SkillsConfig(enabled=True, directories=["codeassist/skills"])
    fp = _write_skill(tmp_path, "demo", "old desc", "old body", "demo")
    reg = SkillRegistry(tmp_path, cfg)
    reg.discover()

    path = reg.update_skill("demo", description="New desc", content="New body", slash_command="demo")
    assert path is not None
    # File on disk now reflects the edit.
    text = path.read_text(encoding="utf-8")
    assert "New desc" in text
    assert "New body" in text

    # A fresh discovery sees the updated values.
    reg2 = SkillRegistry(tmp_path, cfg)
    reg2.discover()
    s = reg2.get_skill("demo")
    assert s.description == "New desc"
    assert s.content == "New body"


def test_remove_skill_deletes_file(tmp_path):
    from codeassist.skills import SkillRegistry
    from codeassist.config import SkillsConfig

    cfg = SkillsConfig(enabled=True, directories=["codeassist/skills"])
    reg = SkillRegistry(tmp_path, cfg)
    fp = _write_skill(tmp_path, "demo", "d", "body", "demo")
    reg.discover()

    path = reg.remove_skill("demo")
    assert path is not None
    assert not fp.exists()


def test_remove_skill_blocks_traversal(tmp_path):
    """A crafted `source` escaping the workspace must not be deletable."""
    from codeassist.skills import SkillRegistry, Skill
    from codeassist.config import SkillsConfig

    cfg = SkillsConfig(enabled=True, directories=["codeassist/skills"])
    reg = SkillRegistry(tmp_path, cfg)
    # Plant a file outside the workspace and point a skill at it via source.
    outside = tmp_path.parent / "escape.md"
    outside.write_text("x", encoding="utf-8")
    reg._skills["escape"] = Skill(
        name="escape", description="d", content="c", slash_command="e",
        source=str(outside.relative_to(tmp_path.parent)),
    )

    assert reg.remove_skill("escape") is None
    assert outside.exists()  # untouched


def test_remove_plugin_deletes_dir(tmp_path):
    from codeassist.plugins import PluginRegistry
    from codeassist.config import PluginConfig

    pdir = tmp_path / "codeassist" / "plugins" / "demo"
    pdir.mkdir(parents=True)
    (pdir / "plugin.py").write_text(
        "def register(config):\n    return {'tools': [], 'hooks': {}}\n",
        encoding="utf-8",
    )
    (pdir / "plugin.json").write_text('{"version": "1.0", "config": {}}', encoding="utf-8")

    cfg = PluginConfig(enabled=True, directories=["codeassist/plugins"])
    reg = PluginRegistry(tmp_path, cfg)
    reg.discover()
    assert reg.get_plugin("demo") is not None

    path = reg.remove_plugin("demo")
    assert path is not None
    assert not pdir.exists()


def test_remove_custom_tool_deletes_file(tmp_path):
    from codeassist.custom_tools_loader import CustomToolRegistry

    cdir = tmp_path / "runtime" / "custom_tools"
    cdir.mkdir(parents=True)
    (cdir / "mytool.py").write_text(
        'TOOLS = {"mytool": {"description": "d", "execute": "execute"}}\n'
        "async def execute(**kwargs):\n    return 'ok'\n",
        encoding="utf-8",
    )
    # trust_registry=None -> loader skips trust checks so the tool discovers.
    reg = CustomToolRegistry(tmp_path)
    reg.discover()
    assert reg.get_tool("mytool") is not None

    path = reg.remove_tool("mytool")
    assert path is not None
    assert not (cdir / "mytool.py").exists()
    assert reg.get_tool("mytool") is None


# --------------------------------------------------------------------------- #
# Route tests                                                                 #
# --------------------------------------------------------------------------- #


@pytest.fixture
def ws_client(tmp_path, monkeypatch):
    """Boot the app with [server] workspace pointed at an isolated tmp dir."""
    (tmp_path / "config.toml").write_text(
        "[server]\nhost = \"127.0.0.1\"\nport = 8090\nworkspace = \"%s\"\n"
        "[skills]\nenabled = true\ndirectories = [\"codeassist/skills\"]\n"
        "[plugins]\nenabled = true\ndirectories = [\"codeassist/plugins\"]\n"
        % str(tmp_path),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    server._config = None
    with TestClient(server.app, raise_server_exceptions=False) as client:
        yield client, tmp_path


def test_update_skill_route(ws_client):
    client, ws = ws_client
    fp = _write_skill(ws, "demo", "old desc", "old body", "demo")
    r = client.put("/api/skills/demo", json={"description": "edited", "content": "edited body"})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    assert fp.read_text().count("edited") >= 1


def test_delete_skill_route_404_and_ok(ws_client):
    client, ws = ws_client
    _write_skill(ws, "demo", "d", "body", "demo")

    assert client.delete("/api/skills/nope").status_code == 404

    fp = next((ws / "codeassist" / "skills").glob("*.md"))
    r = client.delete(f"/api/skills/{fp.name[:-3]}")
    assert r.status_code == 200, r.text
    assert not fp.exists()


def test_delete_plugin_route(ws_client):
    client, ws = ws_client
    pdir = ws / "codeassist" / "plugins" / "demo"
    pdir.mkdir(parents=True)
    (pdir / "plugin.py").write_text("def register(config):\n    return {'tools': [], 'hooks': {}}\n")
    (pdir / "plugin.json").write_text("{}")

    assert client.delete("/api/plugins/demo").status_code == 200, client.get("/api/plugins").text
    assert not pdir.exists()


def test_delete_custom_tool_route_404(ws_client):
    client, ws = ws_client
    # No custom tools exist -> 404.
    assert client.delete("/api/custom-tools/none").status_code == 404
