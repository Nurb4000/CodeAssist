"""Tests for chat file/image attachments (server-side validation + WS wiring)."""
import uuid

import pytest

from codeassist.server import (
    MAX_FILE_BYTES,
    MAX_FILES_PER_MESSAGE,
    _parse_text_files,
    _resolve_text_mime,
)


class TestParseTextFiles:
    def test_empty_returns_no_attachments(self):
        assert _parse_text_files([]) == ([], None)
        assert _parse_text_files(None) == ([], None)

    def test_valid_file_attachments(self):
        files, err = _parse_text_files([{"name": "notes.txt", "content": "hello"}])
        assert err is None
        assert len(files) == 1
        att = files[0]
        assert att["attachment_type"] == "text"
        assert att["file_name"] == "notes.txt"
        assert att["data"] == "hello"

    def test_multiple_files(self):
        files, err = _parse_text_files([
            {"name": "a.py", "content": "x"},
            {"name": "b.md", "content": "y"},
        ])
        assert err is None
        assert len(files) == 2

    def test_too_many_files(self):
        payload = [{"name": f"f{i}", "content": "x"} for i in range(MAX_FILES_PER_MESSAGE + 1)]
        files, err = _parse_text_files(payload)
        assert files == []
        assert "Too many files" in err

    def test_file_too_large(self):
        big = "x" * (MAX_FILE_BYTES + 1)
        files, err = _parse_text_files([{"name": "big.txt", "content": big}])
        assert files == []
        assert "too large" in err

    def test_non_dict_entry_rejected(self):
        files, err = _parse_text_files(["not-a-dict"])
        assert files == []
        assert "Invalid file" in err

    def test_missing_content_rejected(self):
        files, err = _parse_text_files([{"name": "x.txt"}])
        assert files == []
        assert "Invalid file" in err

    def test_default_file_name(self):
        files, err = _parse_text_files([{"content": "hi"}])
        assert err is None
        assert files[0]["file_name"] == "file-1"

    def test_json_attachment_preserves_mime(self):
        # A client-supplied application/json must be stored as-is (not forced to
        # text/plain) so the parse_json tool can classify it.
        files, err = _parse_text_files([
            {"name": "data.json", "content": "{}", "type": "application/json"},
        ])
        assert err is None
        assert files[0]["mime_type"] == "application/json"

    def test_mime_falls_back_to_extension_hint(self):
        # No client mime at all -> infer from the extension.
        files, _ = _parse_text_files([{"name": "config.toml", "content": "a=1"}])
        assert files[0]["mime_type"] in ("application/toml", "text/plain")

    def test_ignores_hostile_client_mime(self):
        # A client lying about its mime to look like something else is not trusted
        # for a non-text type; extension inference wins.
        assert _resolve_text_mime("photo.png", "application/json") == "text/plain"


class TestResolveTextMime:
    @pytest.mark.parametrize("client_type,expected", [
        ("application/json", "application/json"),
        ("TEXT/PLAIN; charset=utf-8", "text/plain"),  # params stripped, lowercased
        ("application/yaml", "application/yaml"),
    ])
    def test_accepts_text_and_structured_hints(self, client_type, expected):
        assert _resolve_text_mime("f.x", client_type) == expected

    def test_known_extension_is_inferred(self):
        # .txt is universally known -> text/plain even with no client hint.
        assert _resolve_text_mime("a.txt", None) == "text/plain"

    def test_uninferable_extension_defaults(self):
        assert _resolve_text_mime("noext", None) == "text/plain"


def _stub_stream_error(monkeypatch):
    import codeassist.llm as llm_mod

    async def _stub_stream(self, *args, **kwargs):
        raise ConnectionError("stubbed LLM for test")

    monkeypatch.setattr(llm_mod.LLMClient, "stream", _stub_stream)


def _drain_until_error(ws, max_reads=60):
    for _ in range(max_reads):
        data = ws.receive_json()
        if data.get("type") == "error":
            return data
    raise AssertionError("never received an 'error' WS message")


def test_ws_rejects_images_when_model_not_vision_capable(live_client, monkeypatch):
    """Images must be rejected with a clear error when the model can't see."""
    import codeassist.capabilities as cap_mod

    async def _not_capable(cfg):
        return False

    monkeypatch.setattr(cap_mod, "model_vision_capable", _not_capable)
    _stub_stream_error(monkeypatch)

    sid = f"attachments-novision-{uuid.uuid4()}"
    tiny_png = (
        "data:image/png;base64,"
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    )
    with live_client.websocket_connect(f"/ws/{sid}") as ws:
        ws.send_json({"type": "user_message", "content": "look", "images": [tiny_png]})
        err = _drain_until_error(ws)
        assert "does not support images" in err["message"]


def test_ws_rejects_non_text_files_inline(live_client, monkeypatch):
    """A file beyond the byte cap must produce a validation error, not a crash."""
    _stub_stream_error(monkeypatch)

    sid = f"attachments-file-{uuid.uuid4()}"
    payload = [{
        "name": "huge.bin",
        "content": "z" * (MAX_FILE_BYTES + 1),
    }]
    with live_client.websocket_connect(f"/ws/{sid}") as ws:
        ws.send_json({"type": "user_message", "content": "", "files": payload})
        err = _drain_until_error(ws)
        assert "too large" in err["message"]


def test_ws_inlines_text_files_as_attachments(live_client, monkeypatch):
    """Text attachments must be persisted as message attachments (no images
    involved, so no vision gating applies)."""
    _stub_stream_error(monkeypatch)

    sid = f"attachments-text-{uuid.uuid4()}"
    files = [{"name": "notes.txt", "content": "remember the milk"}]
    with live_client.websocket_connect(f"/ws/{sid}") as ws:
        ws.send_json({"type": "user_message", "content": "review these", "files": files})
        # LLM is stubbed to error -> agent emits an error/done event afterwards.
        _drain_until_error(ws)

    msgs = live_client.get(f"/api/sessions/{sid}/messages").json()
    user_msgs = [m for m in msgs if m["role"] == "user"]
    assert user_msgs, "expected a stored user message"
    atts = user_msgs[-1].get("attachments") or []
    assert any(a.get("attachment_type") == "text" and a.get("file_name") == "notes.txt" for a in atts)
    stored = next(a for a in atts if a.get("file_name") == "notes.txt")
    assert stored["data"] == "remember the milk"