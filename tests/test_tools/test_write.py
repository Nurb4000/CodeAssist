"""Tests for the write tool's backup naming."""

import pytest

from codeassist.tools.write import WriteTool


class TestWriteToolBackup:
    """An existing file is backed up to `<name>.bak`; a trailing dot in the
    original name must not produce a doubled dot (`foo..bak`)."""

    def _tool(self, tmp_path):
        tool = WriteTool()
        tool.workspace = tmp_path
        return tool

    @pytest.mark.asyncio
    async def test_backup_appends_bak(self, tmp_path):
        tool = self._tool(tmp_path)
        f = tmp_path / "foo.txt"
        f.write_text("old")

        result = await tool.execute(file_path=str(f), content="new")

        assert not result.error
        assert (tmp_path / "foo.txt.bak").read_text() == "old"
        assert f.read_text() == "new"

    @pytest.mark.asyncio
    async def test_trailing_dot_backup_has_no_double_dot(self, tmp_path):
        tool = self._tool(tmp_path)
        f = tmp_path / "foo."
        f.write_text("old")

        result = await tool.execute(file_path=str(f), content="new")

        assert not result.error
        assert (tmp_path / "foo.bak").read_text() == "old"
        assert not (tmp_path / "foo..bak").exists()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("name", "backup"),
        [("plain", "plain.bak"), (".bashrc", ".bashrc.bak"), ("a.tar.gz", "a.tar.gz.bak")],
    )
    async def test_backup_name_shapes(self, tmp_path, name, backup):
        tool = self._tool(tmp_path)
        f = tmp_path / name
        f.write_text("old")

        await tool.execute(file_path=str(f), content="new")

        assert (tmp_path / backup).read_text() == "old"

    @pytest.mark.asyncio
    async def test_new_file_has_no_backup(self, tmp_path):
        tool = self._tool(tmp_path)
        f = tmp_path / "fresh.txt"

        result = await tool.execute(file_path=str(f), content="hello")

        assert not result.error
        assert f.read_text() == "hello"
        assert list(tmp_path.glob("*.bak")) == []
