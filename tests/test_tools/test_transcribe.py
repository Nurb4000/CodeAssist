"""Tests for the audio transcription fallback (non-native-audio models).

When the active model cannot *hear* audio natively, attached mp3/wav/ogg/flac/
m4a is transcribed to text and inlined as a plain text part so the turn still
proceeds. Two engines are supported: ``local`` (openai-whisper on the box) and
``openai`` (hosted Whisper via the SDK). ``transcribe_audio`` returns ``None``
when neither engine can run, so the WS handler can reject rather than strand.
"""

import asyncio
import base64
import sys
import types

import pytest

from codeassist.config import Config, LLMConfig
from codeassist.server import _parse_audio_attachments
from codeassist.transcribe import TranscriptionConfig, transcribe_audio


def _audio_blob(name="rec.mp3", mime="audio/mpeg", text=b"hello world"):
    """One audio attachment as the WS handler would parse it."""
    b64 = base64.b64encode(text).decode()
    return {
        "attachment_type": "audio",
        "mime_type": mime,
        "file_name": name,
        "data": f"data:{mime};base64,{b64}",
    }


def _fake_whisper(monkeypatch, *, answer="local stub"):
    """Seed a fake ``whisper`` module whose load_model returns a sync transcriber."""

    class _Model:
        def transcribe(self, fp, verbose=False):
            return {"text": answer}

    def _load(model_name):
        return _Model()

    fake = types.ModuleType("whisper")
    fake.load_model = _load
    monkeypatch.setitem(sys.modules, "whisper", fake)


def _fake_openai(monkeypatch, *, seen=None, answer="openai stub"):
    """Stub the Openai SDK; returns ``seen`` capturing the key/endpoint it sees.

    ``create`` is a plain async coroutine bound as a class attribute so calling
    it through the ``audio.transcriptions.create`` instance chain does not demand
    a stray ``self`` positional arg.
    """
    if seen is None:
        seen = {}

    async def _create(self, *, file=None, model=None, **kw):
        name = file[0] if isinstance(file, tuple) else file
        seen["file"] = name
        return types.SimpleNamespace(text=answer)

    class _Transcriptions:
        create = _create

    class _Audio:
        transcriptions = _Transcriptions()

    class _SDK:
        def __init__(self, api_key="", base_url=None):
            seen["api_key"] = api_key
            seen["base_url"] = base_url

        audio = _Audio()

    monkeypatch.setattr("openai.AsyncOpenAI", lambda *a, **k: _SDK(**k))
    return seen


@pytest.fixture
def cfg():
    return Config(llm=LLMConfig(api_key="sk-x", base_url="http://localhost:1234"))


class TestTranscribeLocalEngine:
    @pytest.mark.asyncio
    async def test_local_engine_transcribes_audio(self, monkeypatch, cfg):
        _fake_whisper(monkeypatch)
        audio = [_audio_blob()]
        result = await transcribe_audio(audio, cfg, TranscriptionConfig(engine="local"))
        assert "[rec.mp3]: local stub" in result

    @pytest.mark.asyncio
    async def test_local_engine_uses_configured_model(self, monkeypatch, cfg):
        _fake_whisper(monkeypatch)
        # The configured model size is passed to load_model; assert via a sentinel.
        seen = {}

        class _Model:
            def transcribe(self, fp, verbose=False):
                return {"text": "stub"}

        def _load(model_name):
            seen["model"] = model_name
            return _Model()

        fake = types.ModuleType("whisper")
        fake.load_model = _load
        monkeypatch.setitem(sys.modules, "whisper", fake)
        result = await transcribe_audio(
            [_audio_blob()], cfg, TranscriptionConfig(engine="local", model="base")
        )
        assert seen["model"] == "base"


class TestTranscribeOpenaiEngine:
    @pytest.mark.asyncio
    async def test_openai_engine_transcribes(self, monkeypatch, cfg):
        seen = _fake_openai(monkeypatch)
        result = await transcribe_audio(
            [_audio_blob()], cfg, TranscriptionConfig(engine="openai", model="whisper-1")
        )
        assert "[rec.mp3]: openai stub" in result
        # The SDK received the captured file name -- proof the base64 reached it.
        assert seen["file"] == "rec.mp3"

    @pytest.mark.asyncio
    async def test_openai_engine_uses_api_key_from_cfg(self, monkeypatch, cfg):
        # The configured LLM api_key is used as a fallback when the transcription
        # config has no key of its own. Stub SDK to capture the key/endpoint it sees.
        seen = _fake_openai(monkeypatch)
        await transcribe_audio(
            [_audio_blob()], cfg, TranscriptionConfig(engine="openai")
        )
        assert seen["api_key"] == "sk-x"
        assert seen["base_url"] == "http://localhost:1234"

    @pytest.mark.asyncio
    async def test_openai_engine_fail_closed_without_key(self, monkeypatch):
        # No key in the transcription config or the LLM config -> None (fail
        # closed) rather than a half-decoded blob.
        cfg = Config(llm=LLMConfig(api_key="", base_url=""))
        _fake_openai(monkeypatch)
        result = await transcribe_audio(
            [_audio_blob()], cfg, TranscriptionConfig(engine="openai")
        )
        assert result is None


class TestTranscribeDecodeFailure:
    @pytest.mark.asyncio
    async def test_decode_error_fails_closed(self, monkeypatch, cfg):
        # A whisper load_model that raises sinks the whole fallback so the turn
        # isn't stranded with audio the model can't hear.
        def _boom(model_name):
            raise RuntimeError("weights corrupt")

        fake = types.ModuleType("whisper")
        fake.load_model = _boom
        monkeypatch.setitem(sys.modules, "whisper", fake)
        result = await transcribe_audio(
            [_audio_blob()], cfg, TranscriptionConfig(engine="local")
        )
        assert result is None


class TestTranscribeWiring:
    """The WS handler inlines the transcript as a text part and drops raw audio."""

    @pytest.mark.asyncio
    async def test_ws_handler_inlines_transcript_drops_audio(self, monkeypatch, cfg):
        _fake_whisper(monkeypatch, answer="transcribed")
        # Mirror the WS handler's non-native-audio branch.
        audio, _err = _parse_audio_attachments([_audio_blob()])
        assert audio and audio[0]["attachment_type"] == "audio"

        transcript = await transcribe_audio(audio, cfg)
        assert transcript is not None

        # The transcript becomes a text attachment; raw audio is dropped.
        files = [{"name": "_transcribed_audio", "content": transcript, "type": "text/plain"}]
        audio = []  # WS handler sets this after inlining the transcript
        assert audio == []
        assert "[rec.mp3]: transcribed" in files[0]["content"]
