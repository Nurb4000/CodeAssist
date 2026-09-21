"""Docker Tool - Container management for workspace projects."""

import asyncio
import json
import logging
from pathlib import Path

from . import Tool, ToolResult

log = logging.getLogger(__name__)


class DockerTool(Tool):
    name = "docker"
    description = (
        "Manage Docker containers for the workspace. Supports build, run, stop, "
        "remove, list, and logs operations. Auto-detects Dockerfile and "
        "docker-compose.yml in the workspace."
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["build", "run", "stop", "remove", "list", "logs", "ps", "compose_up", "compose_down"],
                "description": "Docker action to perform",
            },
            "target": {
                "type": "string",
                "description": "Image name, container name, or compose service (optional)",
            },
            "args": {
                "type": "string",
                "description": "Extra arguments to pass to the docker command",
            },
        },
        "required": ["action"],
    }

    async def _run_docker(self, cmd: list[str], timeout: int = 120) -> tuple[int, str]:
        """Run a docker command and return exit code + output."""
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return proc.returncode, stdout.decode("utf-8", errors="replace")

    async def execute(self, action: str, target: str | None = None,
                      args: str | None = None) -> ToolResult:
        try:
            workspace = Path(self.workspace) if hasattr(self, "workspace") else Path(".")
            extra = args.split() if args else []

            # Check if docker is available
            try:
                code, _ = await self._run_docker(["docker", "version"], timeout=10)
                if code != 0:
                    return ToolResult(output="Error: Docker is not running or not installed", error=True)
            except (FileNotFoundError, asyncio.TimeoutError):
                return ToolResult(output="Error: Docker is not installed or not in PATH", error=True)

            if action == "build":
                if not (workspace / "Dockerfile").exists():
                    return ToolResult(output="Error: No Dockerfile found in workspace", error=True)
                tag = target or workspace.name.lower()
                code, output = await self._run_docker(
                    ["docker", "build", "-t", tag, "."] + extra,
                    timeout=300,
                )
                return ToolResult(
                    output=f"**Docker build** ({tag}):\n\n{output[-3000:]}",
                    error=code != 0,
                )

            elif action == "run":
                if not target:
                    return ToolResult(output="Error: image name required for run", error=True)
                cmd = ["docker", "run", "-d"] + extra + [target]
                code, output = await self._run_docker(cmd)
                return ToolResult(
                    output=f"**Docker run**:\n{output}",
                    error=code != 0,
                )

            elif action == "stop":
                if not target:
                    return ToolResult(output="Error: container name required for stop", error=True)
                code, output = await self._run_docker(["docker", "stop", target] + extra)
                return ToolResult(
                    output=f"**Docker stop** ({target}):\n{output}",
                    error=code != 0,
                )

            elif action == "remove":
                if not target:
                    return ToolResult(output="Error: container/image name required for remove", error=True)
                code, output = await self._run_docker(["docker", "rm", "-f", target] + extra)
                return ToolResult(
                    output=f"**Docker remove** ({target}):\n{output}",
                    error=code != 0,
                )

            elif action in ("list", "ps"):
                code, output = await self._run_docker(
                    ["docker", "ps", "-a", "--format", "table {{.ID}}\t{{.Names}}\t{{.Status}}\t{{.Image}}"] + extra
                )
                return ToolResult(output=f"**Docker containers:**\n```\n{output}\n```")

            elif action == "logs":
                if not target:
                    return ToolResult(output="Error: container name required for logs", error=True)
                cmd = ["docker", "logs", "--tail", "100"] + extra + [target]
                code, output = await self._run_docker(cmd)
                return ToolResult(
                    output=f"**Docker logs** ({target}):\n```\n{output[-3000:]}\n```",
                    error=code != 0,
                )

            elif action == "compose_up":
                if not (workspace / "docker-compose.yml").exists() and not (workspace / "docker-compose.yaml").exists():
                    return ToolResult(output="Error: No docker-compose.yml found in workspace", error=True)
                cmd = ["docker", "compose", "up", "-d"] + extra
                code, output = await self._run_docker(cmd, timeout=300)
                return ToolResult(
                    output=f"**Docker Compose up**:\n{output[-3000:]}",
                    error=code != 0,
                )

            elif action == "compose_down":
                cmd = ["docker", "compose", "down"] + extra
                code, output = await self._run_docker(cmd)
                return ToolResult(
                    output=f"**Docker Compose down**:\n{output}",
                    error=code != 0,
                )

            else:
                return ToolResult(output=f"Error: unknown action '{action}'", error=True)

        except asyncio.TimeoutError:
            return ToolResult(output="Error: Docker command timed out", error=True)
        except Exception as e:
            log.exception("docker tool failed")
            return ToolResult(output=f"Error: {e}", error=True)
