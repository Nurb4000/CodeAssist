"""Tests for the package_manager command-injection fix (re-scan H1).

The tool used to build a shell string with ``.format(package=package)`` and run
it via ``create_subprocess_shell`` -- so a package name like
``"foo && touch /tmp/pwned"`` was interpreted by the shell. Commands are now
argv lists passed to ``create_subprocess_exec``, so caller-supplied values are
single literal arguments that no shell ever parses.
"""

from unittest.mock import AsyncMock, patch

import pytest

from codeassist.tools.package_manager import PackageManagerTool, get_manager_commands


class TestManagerCommandsAreArgv:
    def test_add_command_is_a_list_without_user_input(self):
        assert get_manager_commands("pip")["add"] == ["pip", "install"]

    def test_every_command_is_a_list(self):
        managers = [
            "pip", "poetry", "npm", "yarn", "pnpm", "go_modules", "cargo", "bundler", "composer",
        ]
        for mgr in managers:
            for action, cmd in get_manager_commands(mgr).items():
                assert isinstance(cmd, list), f"{mgr}.{action} must be an argv list"

    def test_unknown_manager_is_empty(self):
        assert get_manager_commands("not-a-manager") == {}


class TestExecuteNoShell:
    def _tool(self, tmp_path):
        tool = PackageManagerTool()
        tool.workspace = tmp_path
        return tool

    @staticmethod
    async def _stub_exec_returns_ok():
        """Return a context manager stubbing create_subprocess_exec with an ok proc."""

        async def fake_exec(program, *args, **kwargs):
            proc = AsyncMock()
            proc.returncode = 0
            proc.communicate = AsyncMock(return_value=(b"installed\n", None))
            return proc

        return fake_exec

    @pytest.mark.asyncio
    async def test_malicious_package_is_a_single_argv_element(self, tmp_path):
        tool = self._tool(tmp_path)
        captured = {}

        async def fake_exec(program, *args, **kwargs):
            captured["program"] = program
            captured["args"] = args
            proc = AsyncMock()
            proc.returncode = 0
            proc.communicate = AsyncMock(return_value=(b"installed\n", None))
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
            result = await tool.execute(
                action="add", package="foo && touch /tmp/pwned", manager="pip"
            )

        # The whole malicious string arrived as ONE argv element; nothing was
        # split or interpreted by a shell.
        assert captured["program"] == "pip"
        assert captured["args"] == ("install", "foo && touch /tmp/pwned")
        assert "successful" in result.output

    @pytest.mark.asyncio
    async def test_create_subprocess_shell_is_never_called(self, tmp_path):
        tool = self._tool(tmp_path)
        fake_exec = await self._stub_exec_returns_ok()
        with patch("asyncio.create_subprocess_shell") as mock_shell, patch(
            "asyncio.create_subprocess_exec", side_effect=fake_exec
        ):
            await tool.execute(action="add", package="requests", manager="pip")
        mock_shell.assert_not_called()

    @pytest.mark.asyncio
    async def test_pip_install_appends_requirement_file_as_separate_args(self, tmp_path):
        tool = self._tool(tmp_path)
        (tmp_path / "requirements.in").write_text("flask")  # detected over requirements.txt
        captured = {}

        async def fake_exec(program, *args, **kwargs):
            captured["args"] = args
            proc = AsyncMock()
            proc.returncode = 0
            proc.communicate = AsyncMock(return_value=(b"ok\n", None))
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
            await tool.execute(action="install", manager="pip")

        assert captured["args"] == ("install", "-r", "requirements.in")

    @pytest.mark.asyncio
    async def test_remove_appends_package_as_single_arg(self, tmp_path):
        tool = self._tool(tmp_path)
        captured = {}

        async def fake_exec(program, *args, **kwargs):
            captured["args"] = args
            proc = AsyncMock()
            proc.returncode = 0
            proc.communicate = AsyncMock(return_value=(b"ok\n", None))
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
            await tool.execute(action="remove", package="a || b", manager="poetry")

        assert captured["args"] == ("remove", "a || b")
