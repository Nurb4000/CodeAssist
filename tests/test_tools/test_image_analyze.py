"""Tests for the image_analyze base64 streaming encoder and attachment access."""

import asyncio
import base64
import contextlib
import types

import pytest

from codeassist.tools import bind_attachments
from codeassist.tools.image_analyze import ImageAnalyzeTool, _encode_image_base64


@contextlib.contextmanager
def _stub_openai_and_config(monkeypatch, *, answer="A red car."):
    """Point image_analyze at a fake openai client + live config.

    Captures the message sent to the model so tests can assert on the exact
    image_url (disk-encoded vs. raw attachment data URL) that reached the LLM.
    """
    captured = {}

    class _Message:
        content = answer

    class _Choice:
        message = _Message()

    class _Response:
        choices = [_Choice()]

    class _Completions:
        async def create(self, **kwargs):
            captured["messages"] = kwargs["messages"]
            return _Response()

    class _Client:
        def __init__(self, *args, **kwargs):
            self.chat = types.SimpleNamespace(completions=_Completions())

    monkeypatch.setattr("openai.AsyncOpenAI", lambda *a, **k: _Client())
    monkeypatch.setattr(
        "codeassist.tools.image_analyze.live_config",
        lambda: types.SimpleNamespace(
            llm=types.SimpleNamespace(api_key="x", base_url=None, model="gpt-4o"),
        ),
    )
    yield captured


def _image_attachment(data="data:image/png;base64,iVBORw0KGgo=="):
    return {
        "attachment_type": "image",
        "mime_type": "image/png",
        "file_name": "photo.png",
        "data": data,
    }


class TestAttachmentBinding:
    """The ContextVar mechanism that hands tools the current turn's attachments.

    (The ``Tool.attachments()`` accessor is covered by the image_analyze tests,
    which read attachments through it.)
    """

    def test_no_attachments_outside_a_turn(self):
        from codeassist.tools import current_attachments

        assert current_attachments() == []

    def test_bind_returns_attachments_and_resets(self):
        from codeassist.tools import current_attachments

        atts = [{"attachment_type": "image", "mime_type": "image/png",
                 "file_name": "a.png", "data": "data:image/png;base64,AA"}]
        with bind_attachments(atts):
            assert current_attachments() == atts
        assert current_attachments() == []

    def test_bind_with_none_is_empty_not_error(self):
        from codeassist.tools import current_attachments
        with bind_attachments(None):
            assert current_attachments() == []


class TestImageAnalyzeAttachments:
    """Attached images (stored in the message DB, never on disk) must be
    analyzable even when no file_path is given -- the model sees them in
    context but has no disk path to hand image_analyze."""

    @pytest.mark.asyncio
    async def test_analyzes_attached_image_without_file_path(self, monkeypatch):
        with _stub_openai_and_config(monkeypatch) as captured:
            with bind_attachments([_image_attachment()]):
                result = await ImageAnalyzeTool().execute(question="What is it?")

        assert not result.error, result.output
        assert "A red car." in result.output
        # The stored base64 data URL is forwarded verbatim (no re-encode).
        img = next(p for p in captured["messages"][0]["content"] if p["type"] == "image_url")
        assert img["image_url"]["url"].startswith("data:image/png;base64,")

    @pytest.mark.asyncio
    async def test_text_attachments_are_ignored_for_image_analysis(self, monkeypatch):
        attachments = [
            {"attachment_type": "text", "mime_type": "text/plain",
             "file_name": "notes.txt", "data": "hello world"},
            _image_attachment(),
        ]
        with _stub_openai_and_config(monkeypatch) as captured:
            with bind_attachments(attachments):
                result = await ImageAnalyzeTool().execute()
        assert not result.error, result.output
        img = next(p for p in captured["messages"][0]["content"] if p["type"] == "image_url")
        assert img["image_url"]["url"].startswith("data:image/png;base64,")

    @pytest.mark.asyncio
    async def test_no_path_and_no_attachment_is_an_error(self, monkeypatch):
        with _stub_openai_and_config(monkeypatch) as captured:
            with bind_attachments([]):
                result = await ImageAnalyzeTool().execute()
        assert result.error
        assert "no image attached" in result.output.lower()

    @pytest.mark.asyncio
    async def test_disk_path_still_works(self, monkeypatch, tmp_path):
        # Regression: the original file_path entry point must still analyze a
        # real on-disk image (encoded fresh) after the attachment refactor.
        png = b"\x89PNG\r\n\x1a\n" + b"0" * 32  # not a real PNG, just bytes
        path = tmp_path / "pic.png"
        path.write_bytes(png)
        tool = ImageAnalyzeTool()
        tool.workspace = tmp_path  # validate_path requires the file inside workspace
        with _stub_openai_and_config(monkeypatch) as captured:
            result = await tool.execute(file_path=str(path))
        assert not result.error, result.output
        img = next(p for p in captured["messages"][0]["content"] if p["type"] == "image_url")
        # A disk file is base64-encoded by the tool (not a pre-baked data URL).
        assert img["image_url"]["url"].startswith("data:image/png;base64,")
        assert base64.b64decode(img["image_url"]["url"].split(",", 1)[1]) == png

    @pytest.mark.asyncio
    async def test_raw_base64_without_data_header_is_rewrapped(self, monkeypatch):
        # An attachment stored without the `data:...;base64,` prefix must still
        # reach the model as a valid data URL.
        raw = base64.b64encode(b"fakepngbytes").decode()
        att = {"attachment_type": "image", "mime_type": "image/png",
               "file_name": "x.png", "data": raw}
        with _stub_openai_and_config(monkeypatch) as captured:
            with bind_attachments([att]):
                result = await ImageAnalyzeTool().execute()
        assert not result.error, result.output
        img = next(p for p in captured["messages"][0]["content"] if p["type"] == "image_url")
        assert img["image_url"]["url"].startswith("data:image/png;base64,")
        assert img["image_url"]["url"].endswith(raw)

    @pytest.mark.asyncio
    async def test_empty_api_key_still_constructs_client(self, monkeypatch):
        """No-auth local backends (e.g. llama.cpp) ship an empty api_key; the SDK
        rejects a bare empty key at construction. This regression pins the
        fallback sentinel so image analysis works on unauthenticated servers."""
        seen = {}

        class _Message:
            content = "A red car."

        class _Choice:
            message = _Message()

        class _Response:
            choices = [_Choice()]

        class _Completions:
            async def create(self, **kwargs):
                return _Response()

        class _Client:
            def __init__(self, *args, **kwargs):
                seen["api_key"] = kwargs.get("api_key")
                self.chat = types.SimpleNamespace(completions=_Completions())

        monkeypatch.setattr("openai.AsyncOpenAI", lambda *a, **k: _Client(**k))
        monkeypatch.setattr(
            "codeassist.tools.image_analyze.live_config",
            lambda: types.SimpleNamespace(
                llm=types.SimpleNamespace(api_key="", base_url="http://localhost:8080/v1", model="llama"),
            ),
        )

        with bind_attachments([_image_attachment()]):
            result = await ImageAnalyzeTool().execute()

        assert not result.error, result.output
        assert seen["api_key"], "an empty api_key must be normalized to a non-empty sentinel"


class TestImageBase64Encoding:
    """Streaming the base64 encode must produce exactly what encoding the whole
    file at once would, for any size (including non-multiples of 3)."""

    @pytest.mark.parametrize("size", [0, 1, 2, 3, 4, 5, 7, 100, 1023, 1024, 4096])
    def test_single_chunk_matches_plain_encoding(self, tmp_path, size):
        data = bytes((i * 7) % 256 for i in range(size))
        f = tmp_path / "img.bin"
        f.write_bytes(data)
        assert _encode_image_base64(f) == base64.b64encode(data).decode("ascii")

    @pytest.mark.parametrize("size", [6, 9, 15, 100, 1001])
    def test_multiple_small_chunks_match_plain_encoding(self, tmp_path, size):
        data = bytes((i * 13) % 256 for i in range(size))
        f = tmp_path / "img.bin"
        f.write_bytes(data)
        # chunk_size=3 forces one base64 group per chunk; concatenating the
        # per-chunk encodings must equal encoding the whole file at once (any
        # padding only ever lands on the final chunk).
        assert _encode_image_base64(f, chunk_size=3) == base64.b64encode(data).decode("ascii")

    def test_empty_file(self, tmp_path):
        f = tmp_path / "empty.bin"
        f.write_bytes(b"")
        assert _encode_image_base64(f) == ""
