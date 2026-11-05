"""Tests for instruction discovery scope and untrusted-labeling (H4).

Two concerns:
- File discovery must not walk up to filesystem root, so an AGENTS.md in the
  home dir or "/" becomes authoritative model instructions. Discovery is scoped
  to the workspace tree (optionally one parent).
- Remote (fetched) instruction sources must be labeled untrusted in the injected
  block so a poisoned URL injected verbatim into the system prompt is an
  instruction-injection vector, not a silent override.
"""

import pytest

from codeassist.instruction_discovery import (
    InstructionDiscoverer,
    InstructionSource,
)


@pytest.fixture
def discoverer():
    return InstructionDiscoverer()


class TestDiscoveryScope:
    @pytest.mark.asyncio
    async def test_workspace_only_scopes_to_workspace(self, discoverer, tmp_path):
        """scope=workspace inspects only the workspace dir."""
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "AGENTS.md").write_text("workspace-only")

        loaded = await discoverer.discover(
            workspace=ws, scope="workspace", disable_project_config=False
        )
        assert [s.path for s in loaded] == [str(ws / "AGENTS.md")]

    @pytest.mark.asyncio
    async def test_workspace_parent_checks_one_parent(self, discoverer, tmp_path):
        """scope=workspace+parent also checks exactly one parent."""
        ws = tmp_path / "ws"
        ws.mkdir()
        parent = ws.parent
        (parent / "CLAUDE.md").write_text("parent-level")

        loaded = await discoverer.discover(
            workspace=ws, scope="workspace+parent", disable_project_config=False
        )
        assert [s.path for s in loaded] == [str(parent / "CLAUDE.md")]

    @pytest.mark.asyncio
    async def test_root_walk_capped_at_one_parent(self, discoverer, tmp_path):
        """The old default walked to filesystem root; only one parent is now checked."""
        ws = tmp_path / "ws"
        ws.mkdir()
        parent = ws.parent
        grandparent = parent.parent
        (parent / "AGENTS.md").write_text("parent")
        (grandparent / "AGENTS.md").write_text("grandparent")

        loaded = await discoverer.discover(
            workspace=ws, scope="workspace+parent", disable_project_config=False
        )
        # Only the parent is inspected; the grandparent (and beyond) is skipped.
        assert [s.path for s in loaded] == [str(parent / "AGENTS.md")]

    @pytest.mark.asyncio
    async def test_default_scope_is_one_parent(self, discoverer, tmp_path):
        """With no explicit scope, the default is workspace+parent."""
        ws = tmp_path / "ws"
        ws.mkdir()
        parent = ws.parent
        (parent / "AGENTS.md").write_text("parent")

        loaded = await discoverer.discover(
            workspace=ws, disable_project_config=False
        )
        assert [s.path for s in loaded] == [str(parent / "AGENTS.md")]


class TestRemoteLabeling:
    def test_remote_source_labeled_untrusted(self, discoverer):
        """A fetched remote source is labeled untrusted so the model can tell it apart."""
        source = InstructionSource(path="https://example.com/AGENTS.md", content="do this", loaded=True)
        discoverer.sources = [source]

        block = discoverer.get_instruction_block(label_remote=True)
        assert "Untrusted Remote Instructions" in block
        assert "fetched from https://example.com/AGENTS.md" in block

    def test_remote_source_not_labeled_when_disabled(self, discoverer):
        """label_remote=False falls back to the plain filename header."""
        source = InstructionSource(path="https://example.com/AGENTS.md", content="do this", loaded=True)
        discoverer.sources = [source]

        block = discoverer.get_instruction_block(label_remote=False)
        assert "Untrusted Remote Instructions" not in block
        assert "Instructions from https://example.com/AGENTS.md" in block

    def test_local_source_never_labeled_untrusted(self, discoverer):
        """A workspace-owned file is never labeled untrusted."""
        source = InstructionSource(path="/workspace/AGENTS.md", content="do this", loaded=True)
        discoverer.sources = [source]

        block = discoverer.get_instruction_block(label_remote=True)
        assert "Untrusted Remote Instructions" not in block
        assert "Instructions from AGENTS.md" in block
