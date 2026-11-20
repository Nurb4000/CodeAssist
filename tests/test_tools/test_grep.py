"""Tests for grep command construction and filtering (re-scan M5 + follow-up).

The ripgrep path used to place the raw pattern before the search directory with
no leading ``--``, so a pattern beginning with ``-`` (e.g. ``"-foo"``) was parsed
as an rg flag instead of a literal search term. It must now be guarded with
``--``. That guard must NOT move the ``-g`` include/exclude options after the
separator: rg treats everything after ``--`` as a path, so the filters would be
silently ignored (regression from 0af4a01).

Also covers ripgrep error propagation and the Python fallback's directory
exclude / extensionless-file handling.
"""

from unittest.mock import patch

from codeassist.tools.grep import GrepTool


class _FakeResult:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


class TestRipgrepPatternGuard:
    def _tool(self):
        return GrepTool()

    def test_pattern_prefixed_with_dash_is_not_a_flag(self, tmp_path):
        tool = self._tool()
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return _FakeResult()

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
            return _FakeResult()

        with patch("codeassist.tools.grep.subprocess.run", side_effect=fake_run):
            tool._ripgrep("hello", tmp_path, None, None, 0)

        assert "hello" in captured["cmd"]
        assert "--" in captured["cmd"]

    def test_glob_filters_precede_double_dash(self, tmp_path):
        """`-g` after `--` is a path, not an option (the Nov 1 regression)."""
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return _FakeResult()

        with patch("codeassist.tools.grep.subprocess.run", side_effect=fake_run):
            GrepTool()._ripgrep("hello", tmp_path, "*.py", "node_modules", 2)

        cmd = captured["cmd"]
        sep = cmd.index("--")
        assert cmd[:sep] == [
            "rg", "-n", "--max-count", "1000",
            "-C", "2", "-g", "*.py", "-g", "!node_modules",
        ]
        assert cmd[sep + 1:] == ["hello", str(tmp_path)]


class TestRipgrepErrors:
    def test_error_exit_code_is_reported(self, tmp_path):
        def fake_run(cmd, **kwargs):
            return _FakeResult(stderr="rg: regex parse error: unclosed character class", returncode=2)

        with patch("codeassist.tools.grep.subprocess.run", side_effect=fake_run):
            result = GrepTool()._ripgrep("[", tmp_path, None, None, 0)

        assert result.error
        assert "regex parse error" in result.output

    def test_no_matches_exit_code_is_not_an_error(self, tmp_path):
        def fake_run(cmd, **kwargs):
            return _FakeResult(returncode=1)

        with patch("codeassist.tools.grep.subprocess.run", side_effect=fake_run):
            result = GrepTool()._ripgrep("hello", tmp_path, None, None, 0)

        assert not result.error
        assert "No matches found" in result.output


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


class TestPythonGrepFiltering:
    def test_exclude_directory_name(self, tmp_path):
        (tmp_path / "node_modules").mkdir()
        (tmp_path / "node_modules" / "dep.py").write_text("hello")
        (tmp_path / "app.py").write_text("hello")

        result = GrepTool()._python_grep("hello", tmp_path, "*.py", "node_modules", 0)

        assert not result.error
        assert "app.py" in result.output
        assert "node_modules" not in result.output

    def test_exclude_glob_with_slash(self, tmp_path):
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_a.py").write_text("hello")
        (tmp_path / "app.py").write_text("hello")

        result = GrepTool()._python_grep("hello", tmp_path, "*.py", "tests/*", 0)

        assert not result.error
        assert "app.py" in result.output
        assert "test_a.py" not in result.output

    def test_extensionless_files_are_searched(self, tmp_path):
        (tmp_path / "Makefile").write_text("hello")
        (tmp_path / "LICENSE").write_text("hello")

        result = GrepTool()._python_grep("hello", tmp_path, None, None, 0)

        assert not result.error
        assert "Makefile" in result.output
        assert "LICENSE" in result.output
