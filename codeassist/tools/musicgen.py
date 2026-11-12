import base64
import copy
import json
import logging
import uuid
from datetime import datetime
from pathlib import Path

import httpx

from . import Tool, ToolResult, live_config

log = logging.getLogger(__name__)

# MusicGen source (ACE-Step GUI/server). Cited here and in docs so anyone using
# this tool knows which backend the /api/generate contract targets.
MUSICGEN_REFERENCE = "https://github.com/Nurb4000/acestep.cpp-simple-GUI"


def log_musicgen_target(host: str | None = None, port: int | None = None) -> str:
    """Resolve and log the configured musicgen host/port from [musicgen].

    Runtime overrides take precedence over config.toml; falls back to
    localhost:3000 when the section is absent. Returns the ``host:port`` string.
    """
    cfg = live_config()
    mg = getattr(cfg, "musicgen", None)
    resolved_host = host or (mg.host if mg else "localhost")
    resolved_port = int(port) if port is not None else (mg.port if mg else 3000)
    target = f"{resolved_host}:{resolved_port}"
    log.info("musicgen target: %s", target)
    return target


def _new_seed() -> int:
    """A fresh 32-bit-ish seed so batch variants differ (item 9)."""
    return int.from_bytes(uuid.uuid4().bytes[:8], "big") % (2 ** 31 - 1) + 1


def _write(directory: Path, name: str, content) -> None:
    text = content if isinstance(content, str) else json.dumps(content, indent=2)
    (directory / name).write_text(text, encoding="utf-8")


class MusicGenTool(Tool):
    name = "musicgen"
    description = (
        "Generate audio with the external musicgen (ACE-Step) synthesis server. "
        "Sends a song prompt and structured payload to the musicgen /api/generate "
        "endpoint, then saves the prompt, JSON payload, and generated WAV(s) under "
        f"<workspace>/music_generations/. Reference: {MUSICGEN_REFERENCE}"
    )
    parameters = {  # noqa: RUF012
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "Natural-language song prompt (saved to prompt.txt).",
            },
            "payload": {
                "type": "object",
                "description": "Structured ACE-Step params: task_type, caption, lyrics, bpm, duration, keyscale, ...",
            },
            "src_audio": {
                "type": "string",
                "description": "Optional path to a source WAV file (cover/lego/extract/complete). Read + base64-encoded.",
            },
            "ref_audio": {
                "type": "string",
                "description": "Optional path to a reference WAV file (timbre/style control). Read + base64-encoded.",
            },
            "batch_size": {
                "type": "integer",
                "description": "Variants to generate from the same payload, each with a different seed. Default 1.",
            },
            "host": {
                "type": "string",
                "description": "Optional musicgen host (overrides [musicgen] host).",
            },
            "port": {
                "type": "integer",
                "description": "Optional musicgen port (overrides [musicgen] port).",
            },
        },
        "required": ["prompt", "payload"],
    }

    def __init__(self):
        self.workspace = None  # injected by the registry

    async def execute(self, prompt: str, payload: dict, src_audio: str | None = None,
                      ref_audio: str | None = None, batch_size: int = 1,
                      host: str | None = None, port: int | None = None) -> ToolResult:
        try:
            cfg = live_config()
            mg = getattr(cfg, "musicgen", None)
            base_host = host or (mg.host if mg else "localhost")
            base_port = int(port) if port is not None else (mg.port if mg else 3000)
            timeout = float(mg.timeout_seconds if mg else 900)
            base_url = f"http://{base_host}:{base_port}"

            audio = {}
            for field_name, arg in (("src_audio", src_audio), ("ref_audio", ref_audio)):
                entry = self._encode_audio(field_name, arg)
                if entry:
                    audio[field_name] = entry

            n = max(1, int(batch_size))
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            results = []

            for i in range(n):
                item_payload = copy.deepcopy(payload)
                prev = item_payload.get("seed", 0)
                item_payload["seed"] = _new_seed() if prev in (0, None) else int(prev) + i
                out_dir = Path(self.workspace) / "music_generations" / stamp
                if n > 1:
                    out_dir = out_dir / f"item_{i + 1}"
                out_dir.mkdir(parents=True, exist_ok=True)

                body = {"prompt": prompt, "params": item_payload}
                body.update(audio)

                try:
                    async with httpx.AsyncClient(timeout=timeout) as client:
                        resp = await client.post(base_url + "/api/generate", json=body)
                    resp.raise_for_status()
                    data = resp.json()
                except (httpx.HTTPError, httpx.HTTPStatusError) as e:
                    _write(out_dir, "prompt.txt", prompt)
                    _write(out_dir, "payload.json", item_payload)
                    _write(out_dir, "error.json", {"status": "error", "message": f"Request failed: {e}"})
                    results.append({"index": i + 1, "status": "error", "detail": str(e), "dir": str(out_dir)})
                    continue

                if data.get("status") == "success":
                    wav_url = data.get("wav_url", "")
                    wav_path = await self._download_wav(base_url, wav_url)
                    _write(out_dir, "prompt.txt", prompt)
                    _write(out_dir, "payload.json", item_payload)
                    wav_bytes = wav_path.stat().st_size if wav_path and wav_path.exists() else None
                    meta = {
                        "status": "success",
                        "base_filename": data.get("base_filename"),
                        "wav_url": wav_url,
                        "download_url": data.get("download_url"),
                        "wav_saved": str(wav_path) if wav_path and wav_path.exists() else None,
                        "wav_bytes": wav_bytes,
                    }
                    _write(out_dir, "meta.json", meta)
                    results.append({"index": i + 1, "status": "success",
                                    "base_filename": data.get("base_filename"),
                                    "wav_bytes": wav_bytes, "dir": str(out_dir)})
                else:
                    _write(out_dir, "prompt.txt", prompt)
                    _write(out_dir, "payload.json", item_payload)
                    _write(out_dir, "error.json", data)
                    results.append({"index": i + 1, "status": "error",
                                    "detail": data.get("message"), "dir": str(out_dir)})

            summary = {
                "target": base_url,
                "reference": MUSICGEN_REFERENCE,
                "batch_size": n,
                "results": results,
            }
            return ToolResult(output=json.dumps(summary, indent=2))
        except Exception as e:  # noqa: BLE001
            log.exception("musicgen tool failed")
            return ToolResult(output=f"Error: {e}", error=True)

    def _encode_audio(self, field_name: str, arg: str | None) -> dict | None:
        """Turn a WAV path into {filename, b64} for the API body."""
        if not arg:
            return None
        p = Path(arg)
        if not p.is_absolute() and self.workspace:
            p = (self.workspace / p).resolve()
        else:
            p = p.resolve()
        if not p.exists():
            log.warning("musicgen: audio file not found: %s", p)
            return None
        return {"filename": p.name, "b64": base64.b64encode(p.read_bytes()).decode("ascii")}

    async def _download_wav(self, base_url: str, wav_url: str) -> Path | None:
        if not wav_url:
            return None
        full = base_url + wav_url if wav_url.startswith("/") else wav_url
        out = None
        try:
            async with httpx.AsyncClient(timeout=60) as dl:
                wresp = await dl.get(full)
                wresp.raise_for_status()
                stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                out = Path(self.workspace) / "music_generations" / stamp / "output.wav"
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(wresp.content)
                return out
        except httpx.HTTPError as e:
            log.warning("musicgen: WAV download failed: %s", e)
            return out
