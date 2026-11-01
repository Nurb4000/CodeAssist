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
