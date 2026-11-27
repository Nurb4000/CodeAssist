"""Test Runner Tool - Auto-detect framework, run tests, parse results."""

import asyncio
import logging
import os
import re
import shlex
from pathlib import Path

from ..config import RemoteTestVMConfig
from . import Tool, ToolResult, live_config

log = logging.getLogger(__name__)

FRAMEWORK_MARKERS = {
    "pytest": ["pytest.ini", "pyproject.toml", "setup.cfg"],
    "jest": ["jest.config.js", "jest.config.ts", "jest.config.json"],
    "vitest": ["vitest.config.ts", "vitest.config.js"],
    "node_test": ["package.json"],
    "go_test": ["go.mod"],
    "cargo_test": ["Cargo.toml"],
    "maven": ["pom.xml"],
    "gradle": ["build.gradle", "build.gradle.kts"],
}


def detect_framework(workspace: Path) -> str | None:
    """Auto-detect the test framework from project files."""
    for framework, markers in FRAMEWORK_MARKERS.items():
        for marker in markers:
            if (workspace / marker).exists():
                if framework == "go_test" and not (workspace / "go.sum").exists():
                    continue
                return framework
    return None


def get_test_command(framework: str, test_path: str | None, extra_args: str | None) -> list[str]:
    """Build the test command for the detected framework."""
    commands = {
        "pytest": ["python", "-m", "pytest", "-v", "--tb=short"],
        "node_test": ["node", "--test", "tests/js"],
        "jest": ["npx", "jest", "--verbose"],
        "vitest": ["npx", "vitest", "run"],
        "go_test": ["go", "test", "-v", "./..."],
        "cargo_test": ["cargo", "test"],
        "maven": ["mvn", "test"],
        "gradle": ["gradle", "test"],
    }
    cmd = commands.get(framework, ["python", "-m", "pytest", "-v"])
    if test_path:
        if framework == "pytest":
            cmd.append(test_path)
        elif framework == "jest":
            cmd.extend(["--testPathPattern", test_path])
        elif framework == "node_test":
            cmd = ["node", "--test", test_path]
        elif framework == "go_test":
            cmd = ["go", "test", "-v", f"./{test_path}..."]
    if extra_args:
        # shlex keeps quoted values (e.g. -k "some phrase") as one argv token and
        # is also what makes the value safe to re-quote for a remote shell.
        cmd.extend(shlex.split(extra_args))
    return cmd


def parse_pytest_output(output: str) -> dict:
    """Parse pytest output for structured results."""
    result = {"framework": "pytest", "raw": output}
    passed = len(re.findall(r" PASSED", output))
    failed = len(re.findall(r" FAILED", output))
    errors = len(re.findall(r" ERROR", output))
    skipped = len(re.findall(r" SKIPPED", output))
    match = re.search(r"(\d+) passed", output)
    if match:
        passed = int(match.group(1))
    match = re.search(r"(\d+) failed", output)
    if match:
        failed = int(match.group(1))
    result["passed"] = passed
    result["failed"] = failed
    result["errors"] = errors
    result["skipped"] = skipped
    result["total"] = passed + failed + errors + skipped
    result["success"] = failed == 0 and errors == 0
    failures = re.findall(r"(FAILED .+?)(?:\n|$)", output)
    if failures:
        result["failures"] = failures[:10]
    return result


def parse_jest_output(output: str) -> dict:
    """Parse jest output for structured results."""
    result = {"framework": "jest", "raw": output}
    match = re.search(r"Tests:\s+(\d+)\s+passed", output)
    passed = int(match.group(1)) if match else 0
    match = re.search(r"(\d+)\s+failed", output)
    failed = int(match.group(1)) if match else 0
    match = re.search(r"(\d+)\s+skipped", output)
    skipped = int(match.group(1)) if match else 0
    result["passed"] = passed
    result["failed"] = failed
    result["skipped"] = skipped
    result["errors"] = 0
    result["total"] = passed + failed + skipped
    result["success"] = failed == 0
    return result


def parse_node_output(output: str) -> dict:
    """Parse Node's built-in test runner (``node --test``) output."""
    result = {"framework": "node_test", "raw": output}

    def count(label: str) -> int:
        match = re.search(rf"(?:#|\u2139)\s+{label}\s+(\d+)", output)
        return int(match.group(1)) if match else 0

    passed = count("pass")
    failed = count("fail")
    skipped = count("skipped")
    result.update({
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "errors": 0,
        "total": passed + failed + skipped,
        "success": failed == 0,
    })
    return result


def parse_go_output(output: str) -> dict:
    """Parse go test output for structured results."""
    result = {"framework": "go_test", "raw": output}
    passed = len(re.findall(r"^--- PASS:", output, re.MULTILINE))
    failed = len(re.findall(r"^--- FAIL:", output, re.MULTILINE))
    skipped = len(re.findall(r"^--- SKIP:", output, re.MULTILINE))
    result["passed"] = passed
    result["failed"] = failed
    result["skipped"] = skipped
    result["errors"] = 0
    result["total"] = passed + failed + skipped
    result["success"] = failed == 0
    return result


def parse_output(framework: str, output: str) -> dict:
    """Parse test output based on framework."""
    parsers = {
        "pytest": parse_pytest_output,
        "jest": parse_jest_output,
        "node_test": parse_node_output,
        "go_test": parse_go_output,
    }
    parser = parsers.get(framework)
    if parser:
        return parser(output)
    return {"framework": framework, "raw": output, "success": "FAIL" not in output.upper().split("\n")[-1]}


class RemoteVMUnavailable(Exception):
    """Raised when the VM cannot be reached; the only fallback-eligible error."""


def _validate_test_path(test_path: str | None) -> str | None:
    """Reject absolute/escaping paths before they reach a remote shell."""
    if not test_path:
        return None
    candidate = Path(test_path)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError("test_path must be workspace-relative and must not contain '..'")
    return test_path


def _validate_remote_config(remote: RemoteTestVMConfig) -> None:
    if not remote.enabled:
        raise RemoteVMUnavailable("remote test VM is disabled ([remote_test_vm].enabled = false)")
    if not remote.host:
        raise RemoteVMUnavailable(
            "remote test VM has no host; set [remote_test_vm].host or "
            "CODEASSIST_REMOTE_TEST_VM_HOST"
        )
    if not re.fullmatch(r"[A-Za-z0-9._:-]+", remote.host):
        raise RemoteVMUnavailable(f"invalid remote test VM host: {remote.host!r}")
    if remote.user and not re.fullmatch(r"[A-Za-z0-9_.-]+", remote.user):
        raise RemoteVMUnavailable(f"invalid remote test VM user: {remote.user!r}")
    if not 1 <= remote.port <= 65535:
        raise RemoteVMUnavailable(f"invalid remote test VM port: {remote.port}")
    if not remote.remote_dir:
        raise RemoteVMUnavailable("remote test VM has no remote_dir")
    if remote.sync not in {"rsync", "none"}:
        raise RemoteVMUnavailable(f"invalid remote test VM sync mode: {remote.sync!r}")


def _remote_target(remote: RemoteTestVMConfig) -> str:
    return f"{remote.user}@{remote.host}" if remote.user else remote.host


def _ssh_options(remote: RemoteTestVMConfig) -> list[str]:
    """SSH transport options shared by direct commands and rsync's -e."""
    opts = [
        "ssh",
        "-p", str(remote.port),
        "-o", f"ConnectTimeout={max(1, remote.connect_timeout)}",
    ]
    if remote.strict_host_key_checking:
        if remote.known_hosts:
            opts.extend([
                "-o", f"UserKnownHostsFile={remote.known_hosts}",
                "-o", "StrictHostKeyChecking=yes",
            ])
    else:
        opts.extend([
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", "LogLevel=ERROR",
        ])
    if remote.key_path:
        opts.extend(["-i", remote.key_path, "-o", "IdentitiesOnly=yes"])
    return opts


def _with_sshpass(cmd: list[str], remote: RemoteTestVMConfig) -> tuple[list[str], dict[str, str] | None]:
    """Use `sshpass -e` so the password is never visible in argv."""
    if not remote.password:
        return cmd, None
    env = os.environ.copy()
    env["SSHPASS"] = remote.password
    return ["sshpass", "-e", *cmd], env


async def _run_process(
    cmd: list[str],
    *,
    timeout: int,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> tuple[int, str]:
    """Run an argv list, merging stderr into stdout, with a hard timeout."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(cwd) if cwd else None,
            env=env,
        )
    except FileNotFoundError as exc:
        raise RemoteVMUnavailable(f"missing executable: {cmd[0]}") from exc
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=max(1, timeout))
    except TimeoutError as exc:
        proc.kill()
        await proc.communicate()
        raise RemoteVMUnavailable(f"'{cmd[0]}' timed out after {timeout}s") from exc
    return proc.returncode, stdout.decode("utf-8", errors="replace")


async def _ssh_exec(remote: RemoteTestVMConfig, script: str, *, timeout: int) -> tuple[int, str]:
    cmd = [*_ssh_options(remote), _remote_target(remote), script]
    cmd, env = _with_sshpass(cmd, remote)
    return await _run_process(cmd, timeout=timeout, env=env)


async def _resolve_remote_dir(remote: RemoteTestVMConfig) -> str:
    """Expand a leading ``~`` against the remote user's home directory.

    ``remote_dir`` defaults to ``~/codeassist-tests`` on the dedicated VM, not
    ``/tmp``: the latter is shared scratch that is subject to cleanup and is
    not a durable home for synced work. The VM's
    ``$HOME`` has to be queried over SSH -- local ``expanduser()`` would return
    the CodeAssist container's home, which is a different machine.
    """
    remote_dir = remote.remote_dir
    if remote_dir != "~" and not remote_dir.startswith("~/"):
        return remote_dir
    rc, output = await _ssh_exec(remote, 'printf %s "$HOME"', timeout=remote.connect_timeout)
    if rc != 0 or not output.strip():
        raise RemoteVMUnavailable(
            f"could not resolve remote home for remote_dir {remote_dir!r}: {_output_tail(output, 400)}"
        )
    home = output.strip().rstrip("/")
    return home if remote_dir == "~" else f"{home}/{remote_dir[2:]}"


def _rsync_command(workspace: Path, remote: RemoteTestVMConfig, remote_dir: str | None = None) -> tuple[list[str], dict[str, str] | None]:
    source = str(workspace).rstrip("/") + "/"
    remote_dir = remote_dir if remote_dir is not None else remote.remote_dir
    destination = f"{_remote_target(remote)}:{shlex.quote(remote_dir)}/"
    cmd = ["rsync", "-az"]
    cmd.extend(f"--exclude={pattern}" for pattern in remote.rsync_excludes)
    cmd.extend(["-e", shlex.join(_ssh_options(remote)), source, destination])
    return _with_sshpass(cmd, remote)


async def _run_remote(workspace: Path, cmd: list[str], remote: RemoteTestVMConfig) -> tuple[int, str, str]:
    """Preflight SSH, optionally sync the workspace, then run the suite remotely."""
    _validate_remote_config(remote)
    target = _remote_target(remote)

    rc, output = await _ssh_exec(remote, "true", timeout=remote.connect_timeout)
    if rc != 0:
        raise RemoteVMUnavailable(f"SSH preflight failed (exit {rc}): {_output_tail(output, 400)}")
    remote_dir = await _resolve_remote_dir(remote)

    if remote.sync == "rsync":
        quoted_dir = shlex.quote(remote_dir)
        rc, output = await _ssh_exec(remote, f"mkdir -p {quoted_dir}", timeout=remote.connect_timeout)
        if rc != 0:
            raise RemoteVMUnavailable(f"could not create remote dir (exit {rc}): {_output_tail(output, 400)}")

        rsync_cmd, rsync_env = _rsync_command(workspace, remote, remote_dir)
        log.info("Syncing workspace to %s:%s", target, remote_dir)
        rc, output = await _run_process(rsync_cmd, timeout=remote.test_timeout, env=rsync_env)
        if rc != 0:
            raise RemoteVMUnavailable(f"rsync failed (exit {rc}): {_output_tail(output, 400)}")

    script_parts = [f"cd {shlex.quote(remote_dir)}"]
    if remote.remote_env:
        script_parts.append(remote.remote_env)  # admin-controlled config, not model input
    script_parts.append(shlex.join(cmd))
    rc, output = await _ssh_exec(remote, " && ".join(script_parts), timeout=remote.test_timeout)
    return rc, output, f"remote VM ({target})"


async def _run_local(workspace: Path, cmd: list[str]) -> tuple[int, str, str]:
    """Original local execution path, also used as the VM-unavailable fallback."""
    try:
        returncode, output = await _run_process(cmd, timeout=300, cwd=workspace)
    except RemoteVMUnavailable as exc:
        raise RuntimeError(str(exc)) from exc
    return returncode, output, "local container"


def _output_tail(output: str, max_chars: int, max_lines: int = 20) -> str:
    tail = "\n".join(output.splitlines()[-max_lines:])
    return tail[-max_chars:] if len(tail) > max_chars else tail


class TestRunnerTool(Tool):
    name = "test_runner"
    description = (
        "Run tests for the project with automatic framework detection. "
        "Supports pytest, Node's built-in test runner, jest, vitest, go test, cargo test, maven, and gradle. "
        "By default runs on the configured remote test VM (SSH + rsync) and "
        "falls back to local execution only when the VM is unavailable. "
        "Returns structured results with pass/fail counts, failure details, "
        "and the target that produced them."
    )
    parameters = {  # noqa: RUF012
        "type": "object",
        "properties": {
            "test_path": {
                "type": "string",
                "description": "Workspace-relative test file, directory, or pattern to run (optional)",
            },
            "framework": {
                "type": "string",
                "description": "Force a specific framework (optional, auto-detected if omitted)",
            },
            "extra_args": {
                "type": "string",
                "description": "Additional arguments to pass to the test runner (optional)",
            },
            "target": {
                "type": "string",
                "description": "Where to run: 'auto' (remote if configured, else local), 'remote', or 'local'",
                "enum": ["auto", "remote", "local"],
                "default": "auto",
            },
        },
        "required": [],
    }

    async def execute(self, test_path: str | None = None, framework: str | None = None,
                      extra_args: str | None = None, target: str = "auto") -> ToolResult:
        try:
            if target not in {"auto", "remote", "local"}:
                raise ValueError("target must be 'auto', 'remote', or 'local'")
            test_path = _validate_test_path(test_path)

            workspace = Path(self.workspace) if hasattr(self, "workspace") else Path(".")
            cfg = live_config()
            remote: RemoteTestVMConfig = getattr(cfg, "remote_test_vm", RemoteTestVMConfig())
            use_remote = (
                target == "remote"
                or (target == "auto" and bool(remote.enabled and remote.host))
            )

            if not framework:
                framework = detect_framework(workspace)
            if not framework:
                return ToolResult(
                    output="Could not auto-detect test framework. "
                           "Check for pytest.ini, pyproject.toml, jest.config.js, go.mod, or Cargo.toml. "
                           "Or specify framework explicitly.",
                    error=True,
                )

            cmd = get_test_command(framework, test_path, extra_args)
            if not use_remote and cmd and cmd[0] == "python":
                import sys as _sys
                cmd = [_sys.executable, *cmd[1:]]
            log.info("Running tests on %s: %s (framework=%s)", "VM" if use_remote else "local", " ".join(cmd), framework)

            fallback_reason = ""
            if use_remote:
                try:
                    returncode, output, target_label = await _run_remote(workspace, cmd, remote)
                except RemoteVMUnavailable as exc:
                    if target == "remote" or not remote.fallback_to_local:
                        return ToolResult(output=f"Error: remote test VM unavailable: {exc}", error=True)
                    fallback_reason = str(exc)
                    log.warning("Remote test VM unavailable, running locally: %s", fallback_reason)
                    returncode, output, target_label = await _run_local(workspace, cmd)
            else:
                returncode, output, target_label = await _run_local(workspace, cmd)

            results = parse_output(framework, output)
            # Parsers only look for failure markers in the text. Collection/usage
            # errors and "no tests ran" emit none, so a non-zero exit code must
            # still be treated as a failure (otherwise status reads PASS).
            results["success"] = bool(results.get("success")) and returncode == 0
            max_chars = getattr(getattr(cfg, "tools", None), "max_output_chars", 20000)

            lines = [f"**Test Results** ({framework}) — {target_label}\n"]
            if fallback_reason:
                lines.append(f"- Fallback reason: {fallback_reason}")
            lines.append(f"- Passed: {results.get('passed', '?')}")
            lines.append(f"- Failed: {results.get('failed', '?')}")
            if results.get("skipped"):
                lines.append(f"- Skipped: {results['skipped']}")
            lines.append(f"- Total: {results.get('total', '?')}")
            lines.append(f"- Exit code: {returncode}")
            lines.append(f"- Status: {'PASS' if results.get('success') else 'FAIL'}")

            if results.get("failures"):
                lines.append("\n**Failures:**")
                for f in results["failures"][:10]:
                    lines.append(f"  {f}")

            if returncode != 0 and not results.get("failures"):
                tail = _output_tail(output, max_chars)
                lines.append(f"\n**Last 20 lines of output:**\n```\n{tail}\n```")

            return ToolResult(
                output="\n".join(lines),
                error=not results.get("success", False),
            )

        except Exception as e:
            log.exception("test_runner failed")
            return ToolResult(output=f"Error: {e}", error=True)
