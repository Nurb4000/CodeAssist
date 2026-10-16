"""Tests for the Docker tool's workspace handling."""

import pytest

from codeassist.tools.docker_tool import DockerTool


class TestDockerToolWorkspace:
    """A deleted workspace must be reported clearly for workspace-dependent
    actions, while container-by-name actions keep working."""

    @pytest.mark.asyncio
    async def test_missing_workspace_reports_clearly_for_build(self, tmp_path):
        tool = DockerTool()
        tool.workspace = str(tmp_path / "gone")

        calls = []

        async def fake_run(cmd, timeout=120):
            calls.append(cmd)
            return 0, ""

        tool._run_docker = fake_run

        result = await tool.execute(action="build")
        assert result.error is True
        assert "workspace directory does not exist" in result.output
        # Only the docker-availability probe ran; the build never did.
        assert calls == [["docker", "version"]]

    @pytest.mark.asyncio
    async def test_missing_workspace_reports_clearly_for_compose_up(self, tmp_path):
        tool = DockerTool()
        tool.workspace = str(tmp_path / "gone")

        async def fake_run(cmd, timeout=120):
            return 0, ""

        tool._run_docker = fake_run

        result = await tool.execute(action="compose_up")
        assert result.error is True
        assert "workspace directory does not exist" in result.output

    @pytest.mark.asyncio
    async def test_missing_workspace_still_allows_container_by_name_actions(self, tmp_path):
        tool = DockerTool()
        tool.workspace = str(tmp_path / "gone")

        async def fake_run(cmd, timeout=120):
            return 0, "CONTAINER ID   NAMES"

        tool._run_docker = fake_run

        result = await tool.execute(action="list")
        assert result.error is not True
        assert "Docker containers" in result.output

    @pytest.mark.asyncio
    async def test_existing_workspace_without_dockerfile_reports_dockerfile(self, tmp_path):
        tool = DockerTool()
        tool.workspace = str(tmp_path)

        async def fake_run(cmd, timeout=120):
            return 0, ""

        tool._run_docker = fake_run

        result = await tool.execute(action="build")
        assert result.error is True
        assert "No Dockerfile" in result.output
