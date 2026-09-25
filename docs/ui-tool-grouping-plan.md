# UI Plan: Inline Per-Call Tool Grouping

> Living doc for the tool-call rendering change. Updated as progress is made so it
> can be continued across sessions. Last updated: 2026-09-25.

## Context / Problem

Currently all tool calls for an assistant turn are aggregated into a **single
collapsible `.tool-panel`** (one shared header, e.g. "Tool calls (3)", one shared
body). That panel is a visually distinct box separate from the assistant prose.

Feedback from testing: it is hard to correlate a tool call with the *action* that
triggered it — the prose lives in `.message-content` and the tools live in a
separate panel div, so they read as two disconnected "dialogs."

**Goal:** render each tool call as its own collapsible block **inline within the
assistant message flow** (right after the turn's text), collapsed by default —
opencode-style per-call collapse, but folded so it is not messy.

## Compaction Impact (important)

**None.** This is a pure *rendering* change.

- Tool calls are stored per-turn in history regardless of UI: each assistant
  message carries its `tool_calls` (agent.py ~548) followed by separate `tool`
  role result messages (agent.py ~581/665).
- Compaction ("auto token reduction") reads that history via
  `session.get_messages()` → `build_openai_messages` (prompts.py). It never reads
  the UI panels.
- Therefore regrouping tool calls inline in the DOM does not change what the model
  sees, what gets compacted, or compaction thresholds. Storage/model/compaction
  paths are untouched.

## Current State (as implemented)

| Concern | Location | Behavior |
|---|---|---|
| Message shell (per turn) | `app.js` `startAssistantMessage()` (570) | Creates ONE `.tool-panel` div + one `.message-content` div inside the message. |
| Add a tool call | `app.js` `appendToolCall()` (640) | Appends a `.tool-call` into the **shared** `currentToolPanel.body`; bumps `toolCallCount`; sets shared header text. |
| Update a tool result | `app.js` `updateLastToolResult()` (695) | Mutates the **last** `.tool-call` in the shared panel. |
| End a turn | `app.js` `finalizeToolPanel()` (688) | Nulls `currentToolPanel`/`toolCallCount`; panel stays in DOM. |
| Reload from history | `app.js` `loadMessages()` (466) | For each assistant msg with tools: `startAssistantMessage()` → `appendToolCall(...)` per tc; for `tool` msgs: `updateLastToolResult()`. |
| Styles | `style.css` `.tool-panel` (754), `.tool-call` (805) | Shared panel box + per-call blocks inside it. |

State vars: `currentContentEl`, `currentToolStack`, `currentReasoningEl`,
`toolCallCount` (app.js top).

## Target State

- Each assistant turn = one message div containing, in flow order:
  role label → thinking block → **assistant prose** → **one collapsible
  `.tool-call` per tool call** (collapsed by default) → (next turn repeats).
- No shared `.tool-panel` wrapper / single "Tool calls (N)" header. Each tool call
  is its own expand/collapse block attached to the turn's text.
- Tool results update their **corresponding** `.tool-call` inline (by matching the
  `tool_call_id`, not "last one").
- Each call shows a **one-line always-visible preview** (`args → output`) and
  reveals full args/output in an expandable body. This keeps the transcript
  readable (no 500-line dumps) while still showing what each step did at a glance.
- Optional per-message "expand/collapse all" affordance on the turn (nice-to-have,
  not required for v1).

## Implementation Map

Files: `codeassist/static/app.js`, `codeassist/static/style.css`.
Backend (`agent.py`, `session.py`, `tokens.py`, `prompts.py`) — **no changes.**

1. **`startAssistantMessage()`** — drop the separate `.tool-panel` child; add an
   inline tool-call container (e.g. `<div class="tool-call-stack"></div>`) inside
   the message, after `.message-content` (or before — see ordering note). Keep
   `currentContentEl`/`currentReasoningEl`. Introduce `currentToolStack`.
2. **`appendToolCall(name, args, output)`** — append a fresh standalone
   `.tool-call` into `currentToolStack` (collapsed by default via CSS), NOT into a
   shared body/header. Give each block a stable id/`tool_call_id` so results can be
   matched. Remove `toolCallCount`/shared-header logic.
3. **`updateLastToolResult(output)`** → **`updateToolResult(id, output)`** — find the
   `.tool-call` by `tool_call_id` and set its output region; create one if missing.
4. **`finalizeToolPanel()`** → keep a light finalize that nulls
   `currentToolStack`/`currentContentEl`/`currentReasoningEl` for the next turn.
5. **`loadMessages()`** — reload path: build each assistant turn with inline
   `.tool-call` blocks (collapsed), then apply `tool` results by id. Reuse the same
   `appendToolCall`/`updateToolResult` helpers so live and persisted render identically.
6. **`style.css`** — collapse `.tool-panel`/`.tool-panel-body`/`.tool-panel-header`
   rules (or keep for fallback). Ensure `.tool-call` / `.tool-call-body` default to
   collapsed; add `.tool-call-stack` spacing if needed.

**Ordering note:** place tool-call blocks **after** the prose within the message so
the reader sees "what the model said" → "what it did", matching opencode's flow.

## Progress Tracker

- [x] Plan documented (this doc)
- [x] `startAssistantMessage()` inline `.tool-call-stack` container (after prose)
- [x] `appendToolCall(name,args,output,id)` per-call collapsible + stable `data-call-id`
- [x] `updateToolResult(id, output)` matches by id (falls back to most recent)
- [x] `finalizeToolPanel()` resets `currentToolStack`
- [x] `loadMessages()` reload path: per-call blocks + result-by-id (live == persisted)
- [x] CSS: removed `.tool-panel*`; added `.tool-call-stack`; `.tool-call` standalone border/radius
- [x] One-line preview (`args → output`) always visible; full detail in body revealed on expand (`.tool-call.open`)
- [x] `normalizeArgs`/`snippet`/`applyToolCallRender`/`matchToolCall` helpers; results update by stable id
- [ ] Manual smoke test (stream a tool-using turn; reload session) — needs browser
- [x] Full backend suite passes (635)
- [ ] Commit

## Open Questions

- Keep a per-message "expand/collapse all" toggle, or just per-call? (v1: per-call.)
- Should parallel tool calls within one turn render in call order or grouped?
  (Default: arrival/call order, inline.)
- Any concern about very long tool-call stacks visually? (Collapse-by-default mitigates.)
