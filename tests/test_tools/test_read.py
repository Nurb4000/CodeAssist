"""Tests for the read tool's offset/limit handling."""

import pytest

from codeassist.tools.read import ReadTool


def _tool(tmp_path):
    tool = ReadTool()
    tool.workspace = tmp_path
    return tool


@pytest.mark.asyncio
async def test_negative_offset_is_rejected(tmp_path):
    tool = _tool(tmp_path)
    f = tmp_path / "lines.txt"
    f.write_text("a\nb\nc\nd\ne\n", encoding="utf-8")

    result = await tool.execute(file_path=str(f), offset=-1, limit=2)

    assert result.error, "a negative offset must be rejected, not silently misread"
    assert "non-negative" in result.output.lower()


@pytest.mark.asyncio
async def test_negative_limit_is_rejected(tmp_path):
    tool = _tool(tmp_path)
    f = tmp_path / "lines.txt"
    f.write_text("a\nb\nc\nd\ne\n", encoding="utf-8")

    result = await tool.execute(file_path=str(f), offset=0, limit=-5)

    assert result.error, "a negative limit must be rejected, not silently misread"


@pytest.mark.asyncio
async def test_valid_offset_and_limit_still_works(tmp_path):
    tool = _tool(tmp_path)
    f = tmp_path / "lines.txt"
    f.write_text("a\nb\nc\nd\ne\n", encoding="utf-8")

    result = await tool.execute(file_path=str(f), offset=1, limit=2)

    assert not result.error
    assert "2: b" in result.output
    assert "3: c" in result.output
