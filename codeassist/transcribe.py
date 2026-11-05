from __future__ import annotations

"""Audio transcription fallback for non-native-audio models.

When the active model cannot *hear* audio natively (see ``model_audio_capable``),
an attached mp3/wav/ogg/flac/m4a is transcribed to text and inlined as a plain
text part so the turn still proceeds. Two engines are supported:

- ``local`` -- openai-whisper (Python) runs on the box; no network, no API key.
  Baked into the docker image so it works offline.
- ``openai`` -- the Openai SDK's ``audio.transcriptions.create`` call, useful when
  a hosted Whisper endpoint or API key is configured and local encoding is too
  heavy for the hardware.

``transcribe_audio`` returns ``None`` when neither engine can run (model not
loaded, key missing, decode error) so the WS handler can either retry or send a
polite "transcription unavailable" rejection rather than strand the turn.
"""

import asyncio
import base64
import io
import logging
import os
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


@dataclass
class TranscriptionConfig:
    """How to transcribe audio a native-audio model can't hear.

    ``engine`` selects the path; ``model`` is the whisper size (local) or the
    Whisper model id (openai API). ``api_key``/``base_url`` are only used by the
    openai engine and follow the same "empty until set" pattern as the LLM keys.
    """

    engine: str = "local"  # "local" | "openai"
    model: str = "base"  # whisper size (local) or Whisper model id (openai API)
    api_key: str = ""
    base_url: str = ""


def _audio_blobs(attachments: list[dict]) -> list[tuple[str, str]]:
    """Return ``(file_name, base64)`` pairs for audio attachments.

    Accepts a data URL (with its header) or a raw base64 blob; the header is
    informational -- the decoder reads bytes, not the mime.
    """
    out = []
    for att in attachments:
        if att.get("attachment_type") != "audio":
            continue
        name = att.get("file_name") or "audio"
        data = att.get("data") or ""
        if isinstance(data, str) and data.startswith("data:"):
            _, _, b64 = data.partition(",")
        else:
            b64 = data
        out.append((name, b64))
    return out


async def transcribe_audio(
    attachments: list[dict],
    cfg: Config,
    trans_cfg: TranscriptionConfig | None = None,
) -> str | None:
    """Transcribe the audio attachments in ``attachments`` to a single text blob.

    Returns ``None`` when no engine can run (so the caller can reject or retry).
    The active LLM config is threaded to the openai engine for its base_url/key.
    """
    blobs = _audio_blobs(attachments)
    if not blobs:
        return None
    trans_cfg = trans_cfg or TranscriptionConfig()

    parts: list[str] = []
    for name, b64 in blobs:
        text = await _transcribe_one(name, b64, cfg, trans_cfg)
        if text is None:
            # A single failed decode sinks the whole fallback -- fail closed so
            # the turn isn't stranded with audio the model can't hear.
            return None
        parts.append(f"[{name}]: {text}")

    joined = "\n\n".join(parts)
    return joined or None


async def _transcribe_one(
    name: str, b64: str, cfg: LLMConfig, trans_cfg: TranscriptionConfig
) -> str | None:
    """Transcribe one base64 audio blob via the configured engine."""
    if trans_cfg.engine == "openai":
        return await _transcribe_openai(name, b64, cfg, trans_cfg)
    return await _transcribe_local(name, b64, trans_cfg)


async def _transcribe_openai(
    name: str, b64: str, cfg: LLMConfig, trans_cfg: TranscriptionConfig
) -> str | None:
    """Hosted Whisper via the OpenAI SDK's audio.transcriptions.create."""
    # cfg is always supplied by the caller; the fallback guards a bare default.
    # api_key/base_url live on the LLM config, so read them from there.
    api_key = (trans_cfg.api_key or cfg.llm.api_key or "").removeprefix("env:")
    if not api_key and os.environ.get("OPENAI_API_KEY"):
        api_key = os.environ["OPENAI_API_KEY"]
    if not api_key:
        log.info("transcribe: openai engine has no API key; skipping %s", name)
        return None
    try:
        import openai

        client = openai.AsyncOpenAI(
            api_key=api_key,
            # Prefer the transcription-specific endpoint; fall back to the LLM's
            # base_url so a locally-deployed Whisper behind the same server works.
            base_url=trans_cfg.base_url or cfg.llm.base_url or None,
        )
        raw = base64.b64decode(b64, validate=True)
        resp = await client.audio.transcriptions.create(
            file=(name, io.BytesIO(raw), "audio/mpeg"),
            model=trans_cfg.model or "whisper-1",
        )
        return getattr(resp, "text", "") or ""
    except Exception as e:  # noqa: BLE001
        log.warning("transcribe: openai decode of %s failed: %s", name, e)
        return None


async def _transcribe_local(name: str, b64: str, trans_cfg: TranscriptionConfig) -> str | None:
    """Local whisper via openai-whisper. Synchronous CPU work runs in a thread
    pool via ``asyncio.to_thread`` -- never ``run_until_complete``, which hijacks
    the current loop and breaks when called from an async caller (the WS handler)."""
    try:
        import whisper  # imported lazily so the module loads without the dep

        model = await asyncio.to_thread(whisper.load_model, trans_cfg.model or "base")
        raw = base64.b64decode(b64, validate=True)
        result = await asyncio.to_thread(model.transcribe, io.BytesIO(raw), verbose=False)
        return str(result.get("text", "")).strip()
    except Exception as e:  # noqa: BLE001
        log.warning("transcribe: local decode of %s failed: %s", name, e)
        return None
