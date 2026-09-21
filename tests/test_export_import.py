"""Tests for skill/tool export, import, and promote (portability feature).

Covers the registry helpers in ``codeassist.skills`` and
``codeassist.custom_tools_loader`` plus the REST routes that expose them.
"""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import codeassist.server as server
from codeassist.config import SkillsConfig


def _seed_skill(ws: Path, name: str, body: str, slash: str, category: str) -> Path:
    from codeassist.skills import SkillRegistry

    sub = "codeassist/skills" if category == "base" else "runtime/skills"
    d = ws / sub
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{name}.md"
    p.write_text(SkillRegistry.format_skill_file(name, f"Desc {name}", body, slash),
                 encoding="utf-8")
    return p


# --------------------------------------------------------------------------- #
# Skills: export / import / promote                                            #
# --------------------------------------------------------------------------- #


def test_skill_export_categorizes_base_and_custom(tmp_path):
    from codeassist.skills import SkillRegistry

    _seed_skill(tmp_path, "baseone", "bb", "b", "base")
    _seed_skill(tmp_path, "custone", "cc", "c", "custom")

    reg = SkillRegistry(tmp_path, SkillsConfig(enabled=True,
                                              directories=["codeassist/skills", "runtime/skills"]))
    manifest = reg.export_skills()

    assert manifest["format"] == "codeassist-skills-bundle"
    assert manifest["version"] == 1 and manifest["exported_at"]
    by_name = {s["name"]: s for s in manifest["data"]["skills"]}
    assert by_name["baseone"]["category"] == "base"
    assert by_name["custone"]["category"] == "custom"


def test_skill_import_writes_to_category_dir(tmp_path):
    from codeassist.skills import SkillRegistry

    reg = SkillRegistry(tmp_path, SkillsConfig(enabled=True,
                                              directories=["codeassist/skills", "runtime/skills"]))
    reg.discover()
    result = reg.import_skills({
        "format": "codeassist-skills-bundle",
        "data": {
            "skills": [
                {"name": "impbase", "description": "d", "slash": None, "body": "body", "category": "base"},
                {"name": "impcust", "description": "d", "slash": "ic", "body": "body", "category": "custom"},
            ]
        },
    })

    assert result["imported"] == ["codeassist/skills/impbase.md", "runtime/skills/impcust.md"]
    assert (tmp_path / "codeassist/skills/impbase.md").exists()
    assert (tmp_path / "runtime/skills/impcust.md").exists()
    # reload reflects the imported skills
    names = {s.name for s in reg.discover()}
    assert "impbase" in names and "impcust" in names


def test_skill_promote_moves_custom_to_base(tmp_path):
    from codeassist.skills import SkillRegistry

    _seed_skill(tmp_path, "promoteme", "body text", "pp", "custom")
    reg = SkillRegistry(tmp_path, SkillsConfig(enabled=True,
                                              directories=["codeassist/skills", "runtime/skills"]))
    reg.discover()

    target = reg.promote_skill("promoteme")
    assert target == "codeassist/skills/promoteme.md"
    # source file no longer in runtime, now in base
    assert not (tmp_path / "runtime/skills/promoteme.md").exists()
    assert (tmp_path / "codeassist/skills/promoteme.md").exists()

    # promoting again is a no-op (already base)
    assert reg.promote_skill("promoteme") is None
    # unknown skill returns None
    assert reg.promote_skill("nope") is None


def test_skill_import_rejects_wrong_bundle(tmp_path):
    from codeassist.skills import SkillRegistry

    reg = SkillRegistry(tmp_path, SkillsConfig(enabled=True, directories=["codeassist/skills"]))
    with pytest.raises(ValueError):
        reg.import_skills({"format": "not-a-skills-bundle"})


# --------------------------------------------------------------------------- #
# Custom tools: export / import                                              #
# --------------------------------------------------------------------------- #


TOOL_SOURCE = '''"""A sample custom tool."""

TOOLS = {
    "sample": {
        "name": "sample",
        "description": "does a thing",
        "parameters": {"type": "object", "properties": {}},
    }
}


async def execute(**kwargs):
    return "ok"
'''


def test_custom_tool_export_import_roundtrip(tmp_path):
    from codeassist.custom_tools_loader import CustomToolRegistry

    tools_dir = tmp_path / "runtime" / "custom_tools"
    tools_dir.mkdir(parents=True, exist_ok=True)
    (tools_dir / "sample.py").write_text(TOOL_SOURCE, encoding="utf-8")

    reg = CustomToolRegistry(tmp_path, trust_registry=None)
    reg.discover()
    assert reg.get_tool("sample") is not None

    manifest = reg.export_tools()
    assert manifest["format"] == "codeassist-tools-bundle"
    assert manifest["version"] == 1 and manifest["exported_at"]
    assert manifest["data"]["tools"][0]["filename"] == "sample.py"

    # wipe the source and re-import from the manifest
    (tools_dir / "sample.py").unlink()
    result = reg.import_tools(manifest)
    assert result["imported"] == ["runtime/custom_tools/sample.py"]
    assert (tools_dir / "sample.py").read_text(encoding="utf-8") == TOOL_SOURCE
    assert reg.get_tool("sample") is not None


def test_custom_tool_import_rejects_wrong_bundle(tmp_path):
    from codeassist.custom_tools_loader import CustomToolRegistry

    reg = CustomToolRegistry(tmp_path, trust_registry=None)
    with pytest.raises(ValueError):
        reg.import_tools({"format": "wrong"})


def test_export_base_tools_captures_package_source():
    from codeassist.tools import export_base_tools

    payload = export_base_tools()
    assert payload["format"] == "codeassist-base-tools-bundle"
    assert payload["version"] == 1 and payload["exported_at"]
    files = {b["file"] for b in payload["data"]["base_tools"]}
    # a couple of known shipped tool modules should be present, verbatim source
    assert "read.py" in files and "write.py" in files
    read_src = next(b["source"] for b in payload["data"]["base_tools"]
                    if b["file"] == "read.py")
    assert "class ReadTool" in read_src


# --------------------------------------------------------------------------- #
# REST routes                                                                 #
# --------------------------------------------------------------------------- #


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEASSIST_WORKSPACE", str(tmp_path))
    (tmp_path / "config.toml").write_text(
        "[server]\nworkspace = \"%s\"\n[skills]\nenabled = true\n"
        'directories = ["codeassist/skills", "runtime/skills"]\n'
        "[tools]\nenabled = true\n" % str(tmp_path),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    server._config = None
    # The custom-tool registry is a process-wide singleton that boot does not
    # reset; clear it so each test starts from the freshly mounted workspace.
    from codeassist import custom_tools_loader

    custom_tools_loader._custom_tool_registry = None
    with TestClient(server.app, raise_server_exceptions=False) as c:
        yield c, tmp_path


def test_skill_export_import_route(client):
    client, ws = client
    _seed_skill(ws, "routeone", "body", "ro", "custom")

    resp = client.get("/api/skills/export")
    assert resp.status_code == 200
    manifest = resp.json()
    assert manifest["format"] == "codeassist-skills-bundle"

    # remove the skill, then import it back via the route
    (ws / "runtime/skills/routeone.md").unlink()
    body = {"format": "codeassist-skills-bundle",
            "data": {"skills": [{"name": "routeone", "description": "d", "slash": "ro",
                        "body": "body", "category": "custom"}]}}
    r = client.post("/api/skills/import", json=body)
    assert r.status_code == 200, r.text
    assert (ws / "runtime/skills/routeone.md").exists()


def test_skill_promote_route(client):
    client, ws = client
    _seed_skill(ws, "promoteme", "body", "pp", "custom")

    r = client.post("/api/skills/promoteme/promote")
    assert r.status_code == 200, r.text
    assert r.json()["path"] == "codeassist/skills/promoteme.md"
    # promoting a non-custom skill is a 409
    _seed_skill(ws, "baseone", "body", "ba", "base")
    r2 = client.post("/api/skills/baseone/promote")
    assert r2.status_code == 409


def test_custom_tool_export_import_route(client):
    client, ws = client
    tools_dir = ws / "runtime" / "custom_tools"
    tools_dir.mkdir(parents=True, exist_ok=True)
    (tools_dir / "sample.py").write_text(TOOL_SOURCE, encoding="utf-8")

    r = client.get("/api/custom-tools/export")
    assert r.status_code == 200
    manifest = r.json()
    assert manifest["data"]["tools"][0]["filename"] == "sample.py"

    (tools_dir / "sample.py").unlink()
    body = {"format": "codeassist-tools-bundle",
            "data": {"tools": [{"filename": "sample.py", "source": TOOL_SOURCE,
                       "tools": ["sample"], "category": "custom"}]}}
    r2 = client.post("/api/custom-tools/import", json=body)
    assert r2.status_code == 200, r2.text
    assert (tools_dir / "sample.py").exists()


def test_base_tool_export_route(client):
    client, ws = client
    r = client.get("/api/tools/export")
    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload["format"] == "codeassist-base-tools-bundle"
    files = {b["file"] for b in payload["data"]["base_tools"]}
    assert "read.py" in files
