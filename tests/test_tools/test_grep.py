"""Tests for grep command construction (re-scan M5).

The ripgrep path used to place the raw pattern before the search directory with
no leading ``--``, so a pattern beginning with ``-`` (e.g. ``"-foo"``) was parsed
as an rg flag instead of a literal search term. It must now be guarded with
``--``.
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from codeassist.tools.grep import GrepTool


class TestRipgrepPatternGuard:
    def _tool(self):
        return GrepTool()

    def test_pattern_prefixed_with_dash_is_not_a_flag(self, tmp_path):
        tool = self._tool()
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            class _R:
                stdout = ""
            return _R()

        with patch("codeassist.tools.grep.subprocess.run", side_effect=fake_run):
            tool._ripgrep("-foo", tmp_path, None, None, 0)

        # The pattern arrives after a standalone "--" separator, so rg treats
        # the leading-dash pattern as a literal rather than an option.
        assert "--" in captured["cmd"]
        idx = captured["cmd"].index("--")
        assert captured["cmd"][idx + 1] == "-foo"

    def test_normal_pattern_still_works_with_guard(self, tmp_path):
        tool = self._tool()
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            class _R:
                stdout = ""
            return _R()

        with patch("codeassist.tools.grep.subprocess.run", side_effect=fake_run):
            tool._ripgrep("hello", tmp_path, None, None, 0)

        assert "hello" in captured["cmd"]
        assert "--" in captured["cmd"]


class TestPythonGrepScanCap:
    """The pure-Python fallback must not traverse an unbounded tree (re-scan M5)."""

    def test_under_cap_finds_matches(self, tmp_path):
        (tmp_path / "a.txt").write_text("hello world")
        (tmp_path / "b.txt").write_text("hello again")
        tool = GrepTool()
        result = tool._python_grep("hello", tmp_path, None, None, 0)
        assert not result.error
        assert "hello" in result.output

    def test_over_cap_stops_with_notice(self, monkeypatch, tmp_path):
        import codeassist.tools.grep as grep_mod

        monkeypatch.setattr(grep_mod, "MAX_FILES_SCANNED", 5)
        for i in range(8):
            (tmp_path / f"f{i}.txt").write_text("no match anywhere")

        tool = GrepTool()
        result = tool._python_grep("nomatch", tmp_path, None, None, 0)
        assert result.error
        assert "stopped after scanning 5 files" in result.output
