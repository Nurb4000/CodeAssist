"""Tests for chat file/image attachments (server-side validation + WS wiring)."""
import uuid

import pytest

from codeassist.server import _parse_text_files, MAX_FILES_PER_MESSAGE, MAX_FILE_BYTES


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