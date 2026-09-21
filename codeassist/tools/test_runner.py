"""Test Runner Tool - Auto-detect framework, run tests, parse results."""

import asyncio
import logging
import re
from pathlib import Path

from . import Tool, ToolResult

log = logging.getLogger(__name__)

FRAMEWORK_MARKERS = {
    "pytest": ["pytest.ini", "pyproject.toml", "setup.cfg"],
    "jest": ["jest.config.js", "jest.config.ts", "jest.config.json"],
    "vitest": ["vitest.config.ts", "vitest.config.js"],
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
        elif framework == "go_test":
            cmd = ["go", "test", "-v", f"./{test_path}..."]
    if extra_args:
        cmd.extend(extra_args.split())
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
        "go_test": parse_go_output,
    }
    parser = parsers.get(framework)
    if parser:
        return parser(output)
    return {"framework": framework, "raw": output, "success": "FAIL" not in output.upper().split("\n")[-1]}


class TestRunnerTool(Tool):
    name = "test_runner"
    description = (
        "Run tests for the project with automatic framework detection. "
        "Supports pytest, jest, vitest, go test, cargo test, maven, and gradle. "
        "Returns structured results with pass/fail counts and failure details."
    )
    parameters = {
        "type": "object",
        "properties": {
            "test_path": {
                "type": "string",
                "description": "Specific test file, directory, or pattern to run (optional)",
            },
            "framework": {
                "type": "string",
                "description": "Force a specific framework (optional, auto-detected if omitted)",
            },
            "extra_args": {
                "type": "string",
                "description": "Additional arguments to pass to the test runner (optional)",
            },
        },
        "required": [],
    }

    async def execute(self, test_path: str | None = None, framework: str | None = None,
                      extra_args: str | None = None) -> ToolResult:
        try:
            workspace = Path(self.workspace) if hasattr(self, "workspace") else Path(".")

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
            log.info("Running tests: %s (framework=%s)", " ".join(cmd), framework)

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=str(workspace),
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=300)
            output = stdout.decode("utf-8", errors="replace")

            results = parse_output(framework, output)

            lines = [f"**Test Results** ({framework})\n"]
            lines.append(f"- Passed: {results.get('passed', '?')}")
            lines.append(f"- Failed: {results.get('failed', '?')}")
            if results.get("skipped"):
                lines.append(f"- Skipped: {results['skipped']}")
            lines.append(f"- Total: {results.get('total', '?')}")
            lines.append(f"- Exit code: {proc.returncode}")
            lines.append(f"- Status: {'PASS' if results.get('success') else 'FAIL'}")

            if results.get("failures"):
                lines.append("\n**Failures:**")
                for f in results["failures"][:10]:
                    lines.append(f"  {f}")

            if proc.returncode != 0 and not results.get("failures"):
                tail = "\n".join(output.splitlines()[-20:])
                lines.append(f"\n**Last 20 lines of output:**\n```\n{tail}\n```")

            return ToolResult(
                output="\n".join(lines),
                error=not results.get("success", False),
            )

        except asyncio.TimeoutError:
            return ToolResult(output="Error: tests timed out after 300 seconds", error=True)
        except Exception as e:
            log.exception("test_runner failed")
            return ToolResult(output=f"Error: {e}", error=True)
