"""Fingerprints for persisted, non-destructive context checkpoints.

A context checkpoint is a derived cache: it holds the compacted transcript an
agent built for a session, plus the point in that session's message history the
transcript covers. It is deliberately *not* part of message history -- the UI,
export, undo and rollback all keep working against the raw ``messages`` rows.

The two fingerprints in this module are the invalidation contract:

* :func:`compute_history_fingerprint` covers the ordered rows a checkpoint
  stands in for (including attachment data), so an undo, rollback, edit, or
  budget rollover makes an old checkpoint stale.
* :func:`compute_config_fingerprint` covers the system prompt and the settings
  that shape the transcript, so switching agents or compaction modes never
  reuses a transcript built under different rules.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


def _canonical(value: Any) -> str:
    """Stable JSON for hashing: sorted keys, no incidental whitespace."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )


def compute_history_fingerprint(history: list[dict]) -> str:
    """Hash the ordered message rows a checkpoint covers.

    Every field that ``build_openai_messages`` consumes is included, plus the
    row id and timestamp. That makes the fingerprint change when a row is
    edited, deleted, reordered, or gains/loses an attachment -- the exact cases
    where a cached transcript would no longer match visible history.
    """
    digest = hashlib.sha256()
    for msg in history:
        digest.update(_canonical({
            "id": msg.get("id"),
            "role": msg.get("role"),
            "content": msg.get("content"),
            "tool_call_id": msg.get("tool_call_id"),
            "tool_calls": msg.get("tool_calls"),
            "name": msg.get("name"),
            "reasoning_content": msg.get("reasoning_content"),
            "created_at": msg.get("created_at"),
            "attachments": msg.get("attachments") or [],
        }).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def compute_config_fingerprint(system_prompt: str, config) -> str:
    """Hash the prompt and settings that determine a transcript's contents.

    The compaction threshold and output reserve are intentionally absent: they
    decide *when* to compact, not what a compacted transcript looks like, so
    changing them must not throw away a usable checkpoint.
    """
    compaction = config.compaction
    return hashlib.sha256(_canonical({
        "system_prompt": system_prompt,
        "model": config.llm.model,
        "mode": compaction.mode,
        "tail_turns": compaction.tail_turns,
        "keep_recent": compaction.keep_recent,
        "preserve_recent_tokens": compaction.preserve_recent_tokens,
        "tool_result_max_tokens": compaction.tool_result_max_tokens,
    }).encode("utf-8")).hexdigest()
