"""Tests for the LSP client URI handling.

Regression: the LSP ``*`` methods were typed ``uri: str`` but the tool handlers
and apply_patch pass a ``Path``. A ``Path`` embedded in the JSON-RPC params
raises ``TypeError: Object of type PosixPath is not JSON serializable`` inside
``_send_request`` -- before its try block -- so the caller's broad ``except``
recorded it as "not found" and every LSP action silently returned empty.
"""
import pytest

from codeassist.lsp_client import LSPClient


class _FakeProc:
    """Minimal stand-in so methods that check ``self._servers`` reach the send."""

    returncode = None
    stdin = None

    def __init__(self):
        self.sent: list[dict] = []


class TestToUri:
    def test_path_object(self, tmp_path):
        target = tmp_path / "main.py"
        uri = LSPClient._to_uri(target)
        assert uri == target.as_uri()
        assert uri.startswith("file://")

    def test_bare_path_string(self, tmp_path):
        target = tmp_path / "main.py"
        assert LSPClient._to_uri(str(target)) == target.resolve().as_uri()

    def test_file_uri_passthrough(self):
        uri = "file:///tmp/already.py"
        assert LSPClient._to_uri(uri) == uri


@pytest.mark.asyncio
async def test_get_definition_sends_a_uri_not_a_path(tmp_path):
    """A Path argument must reach the request as a JSON-serializable URI string."""
    client = LSPClient()
    client._servers["py"] = _FakeProc()

    captured: dict = {}

    async def _fake_send_request(proc, method, params, timeout=10.0):
        captured["method"] = method
        captured["params"] = params
        return []

    client._send_request = _fake_send_request  # type: ignore[assignment]

    target = tmp_path / "main.py"
    await client.get_definition(target, line=1, character=2)

    assert captured["method"] == "textDocument/definition"
    uri = captured["params"]["textDocument"]["uri"]
    assert isinstance(uri, str) and uri.startswith("file://"), uri
    # And the whole request must be serializable (this is what used to raise).
    import json
    json.dumps(captured["params"])


@pytest.mark.asyncio
async def test_format_document_accepts_a_path(tmp_path):
    client = LSPClient()
    client._servers["py"] = _FakeProc()

    captured: dict = {}

    async def _fake_send_request(proc, method, params, timeout=10.0):
        captured["params"] = params
        return []

    client._send_request = _fake_send_request  # type: ignore[assignment]

    target = tmp_path / "main.py"
    await client.format_document(target, "x = 1\n")
    uri = captured["params"]["textDocument"]["uri"]
    assert isinstance(uri, str) and uri.startswith("file://"), uri


@pytest.mark.asyncio
async def test_get_diagnostics_normalizes_and_matches(tmp_path):
    """get_diagnostics must use the normalized URI for both didOpen and matching."""
    client = LSPClient()
    proc = _FakeProc()
    client._servers["py"] = proc

    sent = {}

    async def _fake_notification(p, method, params):
        sent[method] = params

    client._send_notification = _fake_notification  # type: ignore[assignment]

    target = tmp_path / "main.py"
    await client.get_diagnostics(target, "python")

    uri = sent["textDocument/didOpen"]["textDocument"]["uri"]
    assert isinstance(uri, str) and uri.startswith("file://"), uri
