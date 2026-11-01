"""Package Manager Tool - Detect and run package managers."""

import asyncio
import logging
from pathlib import Path

from . import Tool, ToolResult

log = logging.getLogger(__name__)

MANAGER_MARKERS = {
    "pip": ["requirements.txt", "requirements.in"],
    "pipenv": ["Pipfile"],
    "poetry": ["poetry.lock", "pyproject.toml"],
    "npm": ["package-lock.json", "package.json"],
    "yarn": ["yarn.lock"],
    "pnpm": ["pnpm-lock.yaml"],
    "go_modules": ["go.sum"],
    "cargo": ["Cargo.lock"],
    "bundler": ["Gemfile.lock"],
    "composer": ["composer.lock"],
}


def detect_managers(workspace: Path) -> list[str]:
    """Detect which package managers are used in the project."""
    found = []
    for mgr, markers in MANAGER_MARKERS.items():
        for marker in markers:
            if (workspace / marker).exists():
                found.append(mgr)
                break
    return found


def get_manager_commands(manager: str) -> dict:
    """Get install/update/add/remove commands for a manager as argv lists.

    Commands are stored as argument *lists*, never shell strings, and any
    value supplied by the caller (a package name or requirement file) is
    appended as its own argv element at execution time. Combined with
    ``create_subprocess_exec`` this means no shell is ever involved, so a
    package name such as ``"foo && rm -rf ."`` is installed as a single literal
    argument instead of being interpreted by the shell -- closing the
    command-injection hole that the old ``.format()`` + ``create_subprocess_shell``
    approach had.
    """
    commands = {
        "pip": {"install": ["pip", "install"], "add": ["pip", "install"], "list": ["pip", "list", "--format=columns"]},
        "poetry": {"install": ["poetry", "install"], "add": ["poetry", "add"], "remove": ["poetry", "remove"], "list": ["poetry", "show"]},
        "npm": {"install": ["npm", "install"], "add": ["npm", "install"], "remove": ["npm", "uninstall"], "list": ["npm", "ls", "--depth=0"]},
        "yarn": {"install": ["yarn", "install"], "add": ["yarn", "add"], "remove": ["yarn", "remove"], "list": ["yarn", "list", "--depth=0"]},
        "pnpm": {"install": ["pnpm", "install"], "add": ["pnpm", "add"], "remove": ["pnpm", "remove"], "list": ["pnpm", "list", "--depth=0"]},
        "go_modules": {"install": ["go", "mod", "download"], "add": ["go", "get"], "list": ["go", "list", "-m", "all"]},
        "cargo": {"install": ["cargo", "build"], "add": ["cargo", "add"], "remove": ["cargo", "rm"], "list": ["cargo", "tree", "--depth=1"]},
        "bundler": {"install": ["bundle", "install"], "add": ["bundle", "add"], "list": ["bundle", "list"]},
        "composer": {"install": ["composer", "install"], "add": ["composer", "require"], "list": ["composer", "show"]},
    }
    return commands.get(manager, {})


def parse_package_list(manager: str, output: str) -> list[dict]:
    """Parse package list output into structured format."""
    packages = []
    for line in output.splitlines():
        line = line.strip()
        if not line or line.startswith(("-", "=", "#")):
            continue
        parts = line.split()
        if len(parts) >= 2:
            packages.append({"name": parts[0], "version": parts[1]})
    return packages


class PackageManagerTool(Tool):
    name = "package_manager"
    description = (
        "Manage project dependencies. Auto-detects package managers (pip, poetry, npm, "
        "yarn, pnpm, go, cargo, bundler, composer) and can install, add, remove, or list packages."
    )
    parameters = {  # noqa: RUF012
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["install", "add", "remove", "list", "detect"],
                "description": "Action to perform",
            },
            "package": {
                "type": "string",
                "description": "Package name (for add/remove actions)",
            },
            "manager": {
                "type": "string",
                "description": "Force a specific manager (optional, auto-detected if omitted)",
            },
        },
        "required": ["action"],
    }

    async def execute(self, action: str, package: str | None = None,
                      manager: str | None = None) -> ToolResult:
        try:
            workspace = Path(self.workspace) if hasattr(self, "workspace") else Path(".")

            if action == "detect":
                managers = detect_managers(workspace)
                if not managers:
                    return ToolResult(output="No package managers detected in this project.")
                return ToolResult(output=f"Detected package managers: {', '.join(managers)}")

            if not manager:
                managers = detect_managers(workspace)
                if not managers:
                    return ToolResult(output="Could not auto-detect package manager. Specify --manager explicitly.", error=True)
                manager = managers[0]

            cmds = get_manager_commands(manager)
            if not cmds:
                return ToolResult(output=f"Unknown package manager: {manager}", error=True)

            base = cmds.get(action)
            if action == "list" and not base:
                return ToolResult(output=f"List not supported for {manager}")
            if action == "install" and not base:
                return ToolResult(output=f"Install not supported for {manager}")
            if not base:
                # Unknown action (a value outside the schema enum).
                return ToolResult(output=f"Error: unknown action '{action}'", error=True)

            # Build the argv. User/runtime-supplied values are appended as their
            # own elements so create_subprocess_exec never interprets them as
            # shell syntax -- this is the command-injection fix.
            argv = list(base)
            if action == "install" and manager == "pip":
                req_file = "requirements.txt"
                for f in ["requirements.txt", "requirements.in"]:
                    if (workspace / f).exists():
                        req_file = f
                        break
                argv += ["-r", req_file]
            elif action in ("add", "remove"):
                if not package:
                    return ToolResult(
                        output=f"Error: package name required for {action} action", error=True
                    )
                argv.append(package)

            log.info("Running: %s (manager=%s)", argv, manager)
            proc = await asyncio.create_subprocess_exec(
                argv[0],
                *argv[1:],
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=str(workspace),
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=120)
            output = stdout.decode("utf-8", errors="replace")

            if proc.returncode == 0:
                if action == "list":
                    packages = parse_package_list(manager, output)
                    if packages:
                        lines = [f"**Installed packages** ({len(packages)}):\n"]
                        for p in packages[:50]:
                            lines.append(f"- {p['name']} {p['version']}")
                        return ToolResult(output="\n".join(lines))
                return ToolResult(output=f"**{action.title()} successful** ({manager}):\n\n{output[-2000:]}")
            else:
                return ToolResult(
                    output=f"**{action.title()} failed** ({manager}, exit {proc.returncode}):\n\n{output[-2000:]}",
                    error=True,
                )

        except TimeoutError:
            return ToolResult(output="Error: command timed out after 120 seconds", error=True)
        except Exception as e:
            log.exception("package_manager failed")
            return ToolResult(output=f"Error: {e}", error=True)
