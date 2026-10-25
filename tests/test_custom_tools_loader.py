"""Tests for codeassist.custom_tools_loader — the process-wide singleton and
trust-registry propagation (§8 backlog)."""

from pathlib import Path

import pytest

from codeassist import custom_tools_loader as ctl
from codeassist.custom_tools_loader import CustomToolRegistry
from codeassist.trust_registry import TrustRegistry


@pytest.fixture
def isolated_singleton():
    """The registry is a process-wide singleton; reset it before each test and
    restore the original after, so one test's priming cannot leak into another."""
    original = ctl._custom_tool_registry
    ctl._custom_tool_registry = None
    try:
        yield
    finally:
        ctl._custom_tool_registry = original


def _sentinel():
    """Stand-in for a real TrustRegistry so the test stays hermetic (a real
    registry reads ~/.codeassist/trusted_tools.json on construction)."""
    return object()


def test_singleton_is_reused_for_the_same_workspace(isolated_singleton, tmp_path):
    r1 = ctl.get_custom_tool_registry(tmp_path, trust_registry=_sentinel())
    r2 = ctl.get_custom_tool_registry(tmp_path, trust_registry=_sentinel())
    assert r1 is r2


def test_later_trust_registry_takes_effect_on_reuse(isolated_singleton, tmp_path):
    """An early call with trust_registry=None must not pin trust checks off
    process-wide: a later call supplying a real registry updates the existing
    singleton (§8)."""
    r1 = ctl.get_custom_tool_registry(tmp_path, trust_registry=None)
    assert r1.trust_registry is None

    real = _sentinel()
    r2 = ctl.get_custom_tool_registry(tmp_path, trust_registry=real)

    assert r2 is r1, "the singleton is reused for the same workspace"
    assert r2.trust_registry is real, "the supplied registry propagates on reuse"


def test_explicit_none_does_not_wipe_a_real_registry(isolated_singleton, tmp_path):
    """Once a real registry is in place, a later None call leaves it intact."""
    real = _sentinel()
    r1 = ctl.get_custom_tool_registry(tmp_path, trust_registry=real)

    r2 = ctl.get_custom_tool_registry(tmp_path, trust_registry=None)

    assert r2 is r1
    assert r2.trust_registry is real


def _write_custom_tool(root: Path, name: str = "trustflagtool") -> Path:
    """Create a minimal valid custom tool module under <root>/runtime/custom_tools.

    The stem doubles as the sys.modules name (custom_tool_<stem>); a unique
    default keeps this from colliding with the ``mytool`` module left cached by
    tests/test_disk_registry_edit.py::test_remove_custom_tool_deletes_file.
    """
    tools_dir = root / "runtime" / "custom_tools"
    tools_dir.mkdir(parents=True, exist_ok=True)
    path = tools_dir / f"{name}.py"
    source = (
        'def execute(**kwargs):\n'
        '    return "ok"\n\n'
        'TOOLS = {\n'
        '    "__NAME__": {\n'
        '        "description": "A test custom tool",\n'
        '        "parameters": {"type": "object", "properties": {}},\n'
        '    }\n'
        '}\n'
    )
    path.write_text(source.replace("__NAME__", name), encoding="utf-8")
    return path


def test_approved_custom_tool_loads_marked_trusted(tmp_path):
    """A tool that passed the trust gate must report trusted=True, not the
    CustomTool default of False (§8)."""
    tool_file = _write_custom_tool(tmp_path)
    registry = TrustRegistry(registry_path=tmp_path / "trust.json")
    registry.approve(tool_file)

    loader = CustomToolRegistry(tmp_path, trust_registry=registry)
    loader.discover()

    tool = loader.get_tool("trustflagtool")
    assert tool is not None, "the approved tool should have loaded"
    assert tool.trusted is True, "a loaded custom tool is trusted by definition"
    assert loader.list_tools()[0]["trusted"] is True


def test_remove_tool_clears_in_memory_and_sys_modules(tmp_path):
    """remove_tool must drop the tool from both the in-memory registry and
    sys.modules; a wrong-key ``_modules.pop`` plus unreachable dead code left
    stale module refs behind (§8)."""
    import sys

    tool_file = _write_custom_tool(tmp_path)
    registry = TrustRegistry(registry_path=tmp_path / "trust.json")
    registry.approve(tool_file)

    loader = CustomToolRegistry(tmp_path, trust_registry=registry)
    loader.discover()
    assert loader.get_tool("trustflagtool") is not None

    module_key = f"custom_tool_{tool_file.stem}"
    assert module_key in sys.modules, "precondition: the tool is import-cached"
    assert "trustflagtool" in loader._modules, "precondition: the tool is registered"

    loader.remove_tool("trustflagtool")

    assert "trustflagtool" not in loader._modules, "the in-memory entry is dropped"
    assert module_key not in sys.modules, "the sys.modules entry is dropped (no leak)"
