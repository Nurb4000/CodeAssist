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


@pytest.mark.asyncio
async def test_read_streams_window_instead_of_loading_whole_file(tmp_path, monkeypatch):
    """Reading a small window of a large file must stream the requested lines
    rather than load the whole file with read_text() -- the latter put the entire
    file on the heap before slicing, so a multi-hundred-megabyte log blew memory
    for a 20-line view. Proven by making Path.read_text raise: the tool still
    works, so it can no longer be the path taken."""
    from pathlib import Path

    f = tmp_path / "big.txt"
    with f.open("w", encoding="utf-8") as fh:
        for i in range(10_000):
            fh.write(f"line{i}\n")

    def _boom(*args, **kwargs):
        raise AssertionError("read_text must not be called -- file is streamed")

    monkeypatch.setattr(Path, "read_text", _boom)

    tool = ReadTool()
    tool.workspace = tmp_path
    result = await tool.execute(file_path=str(f), offset=9990, limit=5)

    assert not result.error, result.output
    # Correct window, correctly numbered and footnoted against the real total.
    assert "9991: line9990" in result.output
    assert "9995: line9994" in result.output
    assert "Showing lines 9991-9995 of 10000" in result.output
