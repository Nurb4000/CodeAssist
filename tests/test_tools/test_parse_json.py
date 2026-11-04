"""Tests for the parse_json tool (attachment JSON parsing + mime preservation)."""

import json

import pytest

from codeassist.tools import bind_attachments
from codeassist.tools.parse_json import (
    ParseJsonTool,
    _json_type,
    _resolve_path,
    _tokenize_path,
)


def _tool(tmp_path):
    tool = ParseJsonTool()
    tool.workspace = tmp_path
    return tool


class TestPathTokenizer:
    @pytest.mark.parametrize("path,expected", [
        ("$.a.b", [("key", "a"), ("key", "b")]),
        ("a.b", [("key", "a"), ("key", "b")]),  # bare leading key
        ("$['a']['b']", [("key", "a"), ("key", "b")]),
        ('$["a"]', [("key", "a")]),
        ("arr[0]", [("key", "arr"), ("index", 0)]),
        ("arr[-1]", [("key", "arr"), ("index", -1)]),
        ("$.x[*]", [("key", "x"), ("star", None)]),
    ])
    def test_tokens(self, path, expected):
        assert _tokenize_path(path) == expected

    @pytest.mark.parametrize("path", ["", "$", "a..b", "[", "$[abc]", "$.a[", "$."])
    def test_rejects_malformed(self, path):
        with pytest.raises(ValueError):
            _tokenize_path(path)


class TestPathResolver:
    def sample(self):
        return {
            "store": {"book": [{"title": "T1", "price": 10}, {"title": "T2", "price": 20}]},
            "count": 2,
        }

    def test_nested_key_and_index(self):
        assert _resolve_path(self.sample(), _tokenize_path("$.store.book[1].title")) == "T2"

    def test_negative_index(self):
        assert _resolve_path(self.sample(), _tokenize_path("$.store.book[-1].price")) == 20

    def test_star_maps_over_array(self):
        assert _resolve_path(self.sample(), _tokenize_path("$.store.book[*].title")) == ["T1", "T2"]

    def test_missing_key_raises(self):
        with pytest.raises(KeyError):
            _resolve_path(self.sample(), _tokenize_path("$.nope"))

    def test_index_on_object_raises(self):
        with pytest.raises(IndexError):
            _resolve_path(self.sample(), _tokenize_path("$.count[0]"))

    def test_star_on_non_array_raises(self):
        with pytest.raises(TypeError):
            _resolve_path(self.sample(), _tokenize_path("$.count[*]"))


class TestJsonType:
    def test_distinctions(self):
        assert _json_type(True) == "boolean"
        assert _json_type(1) == "number"
        assert _json_type("x") == "string"
        assert _json_type(None) == "null"
        assert _json_type([1]) == "array"
        assert _json_type({}) == "object"


class TestExecute:
    @pytest.mark.asyncio
    async def test_invalid_json_reports_line_and_column(self, tmp_path):
        tool = _tool(tmp_path)
        (tmp_path / "bad.json").write_text("{ bad json }")
        result = await tool.execute(file_path=str(tmp_path / "bad.json"))
        assert result.error
        assert "invalid JSON" in result.output
        assert "line" in result.output

    @pytest.mark.asyncio
    async def test_extract_by_path(self, tmp_path):
        tool = _tool(tmp_path)
        (tmp_path / "data.json").write_text(json.dumps({"a": {"b": [1, 2, 3]}}))
        result = await tool.execute(path="$.a.b[2]", file_path=str(tmp_path / "data.json"))
        assert not result.error
        assert "3" in result.output

    @pytest.mark.asyncio
    async def test_summarizes_object(self, tmp_path):
        tool = _tool(tmp_path)
        (tmp_path / "d.json").write_text(json.dumps({"name": "x", "n": 1, "flag": True}))
        result = await tool.execute(file_path=str(tmp_path / "d.json"))
        assert not result.error
        assert "Root type: object" in result.output
        assert "'name': string" in result.output
        assert "'n': number" in result.output
        assert "'flag': boolean" in result.output

    @pytest.mark.asyncio
    async def test_summarizes_array_with_type_breakdown(self, tmp_path):
        tool = _tool(tmp_path)
        (tmp_path / "d.json").write_text(json.dumps([1, "a", True, None]))
        result = await tool.execute(file_path=str(tmp_path / "d.json"))
        assert not result.error
        assert "Root type: array" in result.output
        assert "Length: 4 item(s)." in result.output

    @pytest.mark.asyncio
    async def test_no_attachment_and_no_path_is_error(self, tmp_path, monkeypatch):
        tool = _tool(tmp_path)
        monkeypatch.setattr(tool, "attachments", lambda: [])
        result = await tool.execute()
        assert result.error
        assert "no JSON attachment" in result.output

    @pytest.mark.asyncio
    async def test_parses_attached_json(self, tmp_path):
        tool = _tool(tmp_path)
        attachment = {
            "attachment_type": "text",
            "mime_type": "application/json",
            "file_name": "config.json",
            "data": json.dumps({"host": "localhost", "port": 5432}),
        }
        with bind_attachments([attachment]):
            result = await tool.execute(path="$.host")
        assert not result.error
        assert "localhost" in result.output

    @pytest.mark.asyncio
    async def test_matches_json_by_extension_even_without_mime(self, tmp_path):
        tool = _tool(tmp_path)
        # A stale attachment stored as text/plain (mime not preserved) still
        # resolves via the .json file name.
        attachment = {
            "attachment_type": "text",
            "mime_type": "text/plain",
            "file_name": "data.json",
            "data": json.dumps({"ok": True}),
        }
        with bind_attachments([attachment]):
            result = await tool.execute()
        assert not result.error
        assert "Root type: object" in result.output

    @pytest.mark.asyncio
    async def test_non_json_attachment_ignored(self, tmp_path):
        tool = _tool(tmp_path)
        attachments = [{
            "attachment_type": "text",
            "mime_type": "text/plain",
            "file_name": "notes.txt",
            "data": "not json",
        }]
        with bind_attachments(attachments):
            result = await tool.execute()
        assert result.error
        assert "no JSON attachment" in result.output

    @pytest.mark.asyncio
    async def test_empty_document_is_error(self, tmp_path):
        tool = _tool(tmp_path)
        (tmp_path / "e.json").write_text("")
        result = await tool.execute(file_path=str(tmp_path / "e.json"))
        assert result.error
        assert "empty" in result.output.lower()

    @pytest.mark.asyncio
    async def test_path_outside_workspace_is_error(self, tmp_path):
        tool = _tool(tmp_path)
        result = await tool.execute(file_path="/etc/passwd")
        assert result.error
        assert "workspace" in result.output.lower() or "outside" in result.output.lower()

