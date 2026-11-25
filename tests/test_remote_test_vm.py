"""Tests for the SSH-backed remote test VM path in test_runner."""

from pathlib import Path

import pytest

from codeassist.config import Config, RemoteTestVMConfig
from codeassist.tools import test_runner


def _config_with_host(**overrides) -> Config:
    cfg = Config()
    cfg.remote_test_vm.host = "10.0.1.50"
    cfg.remote_test_vm.user = "tester"
    for key, value in overrides.items():
        setattr(cfg.remote_test_vm, key, value)
    return cfg


class TestRemoteTestVMConfig:
    def test_defaults_keep_local_fallback_available(self):
        cfg = Config()
        # Remote is preferred, but no host means the tool falls back locally.
        assert cfg.remote_test_vm.enabled is True
        assert cfg.remote_test_vm.host == ""
        assert cfg.remote_test_vm.sync == "rsync"
        assert cfg.remote_test_vm.fallback_to_local is True
        assert cfg.remote_test_vm.strict_host_key_checking is True
        assert cfg.remote_test_vm.remote_dir == "~/codeassist-tests"

    def test_toml_table_is_loaded(self, tmp_path):
        config_file = tmp_path / "config.toml"
        config_file.write_text("""
[remote_test_vm]
enabled = true
host = "vm.example.lan"
port = 2222
user = "ci"
remote_dir = "/srv/tests"
key_path = "/auth/ssh/id_ed25519"
known_hosts = "/auth/ssh/known_hosts"
sync = "none"
connect_timeout = 9
test_timeout = 600
fallback_to_local = false
strict_host_key_checking = false
remote_env = "source /opt/venv/bin/activate"
rsync_excludes = [".git", "node_modules"]
""")
        cfg = Config.load(config_file)
        rvm = cfg.remote_test_vm
        assert (rvm.host, rvm.port, rvm.user) == ("vm.example.lan", 2222, "ci")
        assert rvm.remote_dir == "/srv/tests"
        assert rvm.key_path == "/auth/ssh/id_ed25519"
        assert rvm.sync == "none"
        assert rvm.connect_timeout == 9
        assert rvm.test_timeout == 600
        assert rvm.fallback_to_local is False
        assert rvm.strict_host_key_checking is False
        assert rvm.remote_env == "source /opt/venv/bin/activate"
        assert rvm.rsync_excludes == [".git", "node_modules"]

    def test_password_env_reference_is_resolved(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VM_PASSWORD", "s3cret")
        config_file = tmp_path / "config.toml"
        config_file.write_text('[remote_test_vm]\npassword = "env:VM_PASSWORD"\n')
        assert Config.load(config_file).remote_test_vm.password == "s3cret"

    def test_env_overrides_win_but_empty_env_is_ignored(self, tmp_path, monkeypatch):
        config_file = tmp_path / "config.toml"
        config_file.write_text("""
[remote_test_vm]
host = "from-toml"
user = "toml-user"
port = 22
sync = "rsync"
""")
        monkeypatch.setenv("CODEASSIST_REMOTE_TEST_VM_HOST", "from-env")
        monkeypatch.setenv("CODEASSIST_REMOTE_TEST_VM_PORT", "2222")
        monkeypatch.setenv("CODEASSIST_REMOTE_TEST_VM_USER", "")
        cfg = Config.load(config_file)
        assert cfg.remote_test_vm.host == "from-env"
        assert cfg.remote_test_vm.port == 2222
        assert cfg.remote_test_vm.user == "toml-user"
        assert cfg.remote_test_vm.sync == "rsync"

    def test_bool_env_override(self, tmp_path, monkeypatch):
        config_file = tmp_path / "config.toml"
        config_file.write_text("[remote_test_vm]\nfallback_to_local = true\n")
        monkeypatch.setenv("CODEASSIST_REMOTE_TEST_VM_FALLBACK_TO_LOCAL", "no")
        assert Config.load(config_file).remote_test_vm.fallback_to_local is False

    def test_invalid_toml_sync_is_rejected(self, tmp_path):
        config_file = tmp_path / "config.toml"
        config_file.write_text('[remote_test_vm]\nsync = "scp"\n')
        with pytest.raises(ValueError, match="remote_test_vm.sync"):
            Config.load(config_file)


class TestRemoteCommandConstruction:
    def test_ssh_options_use_key_and_strict_host_keys(self):
        rvm = RemoteTestVMConfig(
            host="vm", port=2222, user="ci",
            key_path="/auth/ssh/id", known_hosts="/auth/ssh/known_hosts",
            connect_timeout=7,
        )
        opts = test_runner._ssh_options(rvm)
        assert "ssh" in opts
        assert "-p" in opts and "2222" in opts
        assert "-i" in opts and "/auth/ssh/id" in opts
        assert "-o" in opts
        assert "ConnectTimeout=7" in opts
        assert "UserKnownHostsFile=/auth/ssh/known_hosts" in opts
        assert "StrictHostKeyChecking=yes" in opts

    def test_ssh_options_can_disable_strict_checking(self):
        opts = test_runner._ssh_options(RemoteTestVMConfig(host="vm", strict_host_key_checking=False))
        assert "StrictHostKeyChecking=no" in opts
        assert "UserKnownHostsFile=/dev/null" in opts

    def test_sshpass_password_is_not_in_argv(self):
        rvm = RemoteTestVMConfig(host="vm", password="s3cret")
        cmd, env = test_runner._with_sshpass(["ssh", "vm", "true"], rvm)
        assert cmd[:2] == ["sshpass", "-e"]
        assert "s3cret" not in cmd
        assert env["SSHPASS"] == "s3cret"

    def test_extra_args_are_split_with_shell_quoting(self):
        cmd = test_runner.get_test_command("pytest", None, '-k "slow and flaky"')
        assert cmd[-2:] == ["-k", "slow and flaky"]

    @pytest.mark.parametrize("bad_path", ["/etc/passwd", "../secret", "tests/../../secret"])
    def test_test_path_must_be_workspace_relative(self, bad_path):
        with pytest.raises(ValueError, match="workspace-relative"):
            test_runner._validate_test_path(bad_path)

    def test_relative_test_path_is_allowed(self):
        assert test_runner._validate_test_path("tests/test_api.py") == "tests/test_api.py"


class TestRemoteDirResolution:
    @pytest.mark.asyncio
    async def test_absolute_dir_needs_no_home_lookup(self, monkeypatch):
        async def unexpected(*args, **kwargs):
            pytest.fail("absolute remote_dir must not trigger a home lookup")

        monkeypatch.setattr(test_runner, "_ssh_exec", unexpected)
        resolved = await test_runner._resolve_remote_dir(
            RemoteTestVMConfig(remote_dir="/srv/tests")
        )
        assert resolved == "/srv/tests"

    @pytest.mark.asyncio
    async def test_tilde_expands_against_remote_home(self, monkeypatch):
        scripts = []

        async def fake_ssh(remote, script, *, timeout):
            scripts.append(script)
            return 0, "/home/tester\n"

        monkeypatch.setattr(test_runner, "_ssh_exec", fake_ssh)
        resolved = await test_runner._resolve_remote_dir(
            RemoteTestVMConfig(remote_dir="~/codeassist-tests")
        )
        assert resolved == "/home/tester/codeassist-tests"
        assert scripts == ['printf %s "$HOME"']

    @pytest.mark.asyncio
    async def test_failed_home_lookup_is_unavailable_error(self, monkeypatch):
        async def fake_ssh(remote, script, *, timeout):
            return 1, "sh: HOME: not set"

        monkeypatch.setattr(test_runner, "_ssh_exec", fake_ssh)
        with pytest.raises(test_runner.RemoteVMUnavailable, match="could not resolve remote home"):
            await test_runner._resolve_remote_dir(RemoteTestVMConfig(remote_dir="~/codeassist-tests"))

    def test_rsync_uses_resolved_dir(self):
        remote = _config_with_host().remote_test_vm
        cmd, _ = test_runner._rsync_command(Path("/workspace"), remote, "/home/tester/codeassist-tests")
        assert any(arg.endswith(":/home/tester/codeassist-tests/") for arg in cmd)


class TestRunnerDispatch:
    @pytest.fixture
    def tool(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'demo'\n")
        tool = test_runner.TestRunnerTool()
        tool.workspace = str(tmp_path)
        return tool

    @pytest.mark.asyncio
    async def test_falls_back_only_with_fallback_reason(self, tool, monkeypatch):
        monkeypatch.setattr(test_runner, "live_config", lambda: _config_with_host())
        monkeypatch.setattr(
            test_runner, "_run_remote",
            _raise_unavailable("SSH preflight failed (exit 255)"),
        )
        called = []

        async def fake_local(workspace, cmd):
            called.append(cmd)
            return 0, "1 passed", "local container"

        monkeypatch.setattr(test_runner, "_run_local", fake_local)
        result = await tool.execute()
        assert called, "local fallback should run"
        assert "local container" in result.output
        assert "Fallback reason: SSH preflight failed (exit 255)" in result.output
        assert result.error is False

    @pytest.mark.asyncio
    async def test_explicit_remote_target_does_not_fall_back(self, tool, monkeypatch):
        monkeypatch.setattr(test_runner, "live_config", lambda: _config_with_host())
        monkeypatch.setattr(
            test_runner, "_run_remote",
            _raise_unavailable("connection refused"),
        )
        called = []

        async def fake_local(workspace, cmd):
            called.append(cmd)
            return 0, "1 passed", "local container"

        monkeypatch.setattr(test_runner, "_run_local", fake_local)
        result = await tool.execute(target="remote")
        assert not called
        assert result.error is True
        assert "remote test VM unavailable" in result.output

    @pytest.mark.asyncio
    async def test_remote_success_reports_target(self, tool, monkeypatch):
        monkeypatch.setattr(test_runner, "live_config", lambda: _config_with_host())

        async def fake_remote(workspace, cmd, remote):
            return 0, "2 passed", "remote VM (tester@10.0.1.50)"

        async def fake_local(workspace, cmd):
            pytest.fail("local path must not run when the VM succeeds")

        monkeypatch.setattr(test_runner, "_run_remote", fake_remote)
        monkeypatch.setattr(test_runner, "_run_local", fake_local)
        result = await tool.execute()
        assert "remote VM (tester@10.0.1.50)" in result.output
        assert "Fallback reason" not in result.output
        assert result.error is False

    @pytest.mark.asyncio
    async def test_local_target_bypasses_remote(self, tool, monkeypatch):
        monkeypatch.setattr(test_runner, "live_config", lambda: _config_with_host())

        async def fake_remote(workspace, cmd, remote):
            pytest.fail("remote path must not run for target=local")

        async def fake_local(workspace, cmd):
            return 0, "1 passed", "local container"

        monkeypatch.setattr(test_runner, "_run_remote", fake_remote)
        monkeypatch.setattr(test_runner, "_run_local", fake_local)
        result = await tool.execute(target="local")
        assert "local container" in result.output


def _raise_unavailable(message):
    async def raiser(workspace, cmd, remote):
        raise test_runner.RemoteVMUnavailable(message)
    return raiser
