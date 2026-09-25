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
6. **`style.css`** — collapse `.tool-panel*`/`.tool-panel-body`/`.tool-panel-header`
   rules (or keep for fallback). Ensure `.tool-call` / `.tool-call-body` default to
   collapsed; add `.tool-call-stack` spacing if needed.

### Phase 2 architecture (confirmed approach)

**State (app.js top):** `pendingUnit` (detached `.message` streaming a turn's leading
prose/reasoning live), `pendingProseBuf`, `pendingReasonBuf`, `activeStepEl` (committed
work step in the active zone), `ranTools` (did the previous turn use tools? → new-step
boundary), `workStepCount`, `workBlockEl`/`workActiveEl`/`workHistoryEl`, `lastUserEl`.

**Turn model:** a *work step* = one tool-using LLM turn. Leading prose/reasoning stream
into a detached `pendingUnit`; the first `tool_call` **commits** it into the active zone
(`commitPendingUnit`). Prose arriving after tools ran (`ranTools`) closes the active step
(moves to collapsed history) and starts a new pending unit. Consecutive tool-only turns
merge into the open step (no prose boundary). A final prose-only turn is **flushed**
(`flushPendingToMainFlow`) as a normal assistant message in the main flow.

**Work block** (lazy, inserted after `lastUserEl`): header `Work (N) ▸` toggles history
collapse only; `.work-active` always visible (the live step); `.work-history` holds
completed, collapsed steps. Each `.work-step` = header `▸ Step N` (toggles its own open)
+ content(thinking + prose + `.tool-call-stack`).

**Key handlers:** `handleProse` (buffer/stream into pendingUnit, close on `ranTools`),
`onToolCall` (`ranTools=true`; commit pending if no active step; append call),
`onToolResult` (update by id in active/pending step), `onReasoning` (stream into
pendingUnit), `commitPendingUnit`, `closeActiveStep`, `flushPendingToMainFlow`,
`finalizeState` (replaces `finalizeToolPanel`).

**Reload:** consecutive assistant-with-tools turns merge into one work step;
assistant-without-tools turns flush to main flow; `tool` results match by id.

**Ordering:** work block inserted right after the user message; main-flow messages
(summaries/errors/done) appended to end of `messagesEl`. Safe because work steps always
commit before the final summary flushes; a pre-work error ends the run (no work block).

**No backend changes.** Frontend has no DOM test harness — validate with `node --check`
+ manual browser smoke test (stream a multi-step turn; confirm active step is live +
expanded, completes → collapsed history, final summary lands in main flow; reload
reconstructs identically).

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
- [x] Manual smoke test (Chromium headless, real index.html + app.js) — PASS (2026-09-25):
      MID_ACTIVE=1 / MID_HISTORY=0 mid-run; FINAL_ACTIVE=0 / FINAL_HISTORY=2 after endRun;
      summary flushed to main flow (MAIN_FLOW_ASSISTANTS=1); per-step previews render
      (`read`→`shell`); hasFollowUpContent()=true. Harness in /tmp/harness/{stub_inline.js,
      drive_inline.js, gen.py, run_smoke.py} (ephemeral — rebuild with gen.py).
- [x] Docker smoke test (build codeassist:ui-smoke, run container, curl /health + static
      assets 200, drive container-served app.js headless) — PASS (2026-09-25): identical DOM
      assertions to local run. Container serves byte-identical app.js (68711 bytes).
- [x] Full backend suite passes (635)

## Open Questions

- Keep a per-message "expand/collapse all" toggle, or just per-call? (v1: per-call.)
- Should parallel tool calls within one turn render in call order or grouped?
  (Default: arrival/call order, inline.)
- Any concern about very long tool-call stacks visually? (Collapse-by-default mitigates.)

---

## Phase 2: Work Block (confirmed 2026-09-25)

Separate **work** (tool-using steps) from **results** (summaries/final prose). Work
steps live in a dedicated collapsible **Work** block under the user message; results
stay in the main flow. Lets the user watch the current step without being buried by
past steps.

### Design (confirmed)

- **Work block** = collapsible section under the user prompt, split into two zones:
  - **Active zone** (top): the step currently executing, shown **expanded** so you
    can watch it live. **Always visible while a run is active**, even when the
    block is collapsed. (Confirmed: active step stays visible; only completed steps
    hide behind the toggle.)
  - **History zone** (below): completed work steps, **collapsed by default** — the
    accumulator you don't have to look at unless you want to.
- A **step** = one LLM turn that *uses tools* (its prose + tool calls grouped as one
  unit). Pure-prose turns (summaries, final answer) are NOT work steps — they render
  in the main flow and never move into the Work block.
- On completion, a step's DOM unit **moves** from the active zone into the collapsed
  history zone; the next tool-using turn takes over the active zone.
- Reload / past sessions: no live run → all work steps collapsed in history, active
  zone empty. Live "watch it work" only happens during an active run.

### Step-boundary detection (streaming)

Track whether the current step has executed tools since its last prose (`ranTools`).
- `text_delta`: if `ranTools` is true → a **new step** is starting. Close the active
  step (collapse + move to history), start a fresh active step, clear `ranTools`.
  Else append prose to the current active step.
- `tool_call`: set `ranTools = true`; render the call into the active step's work area.
- `tool_result`: update the matching call by id in the active step (live).
- A final prose-only turn (no tools) → renders in main flow, not the Work block.

### Implementation map

Files: `codeassist/static/app.js`, `codeassist/static/style.css`. Backend unchanged.

1. Add `.work-block` container (`.work-active` + `.work-history`) to the user message
   area, created when the first tool-using step begins (lazy — no block for prose-only
   sessions). Toggle header "Work (N)" collapses history; active step stays visible.
2. Introduce `activeStepEl` (current work-step DOM) + `ranTools` flag + `workStepCount`.
3. Rework `ws.onmessage`:
   - `text_delta` → new-step close logic (see above); prose for work steps goes into
     `activeStepEl.prose`, prose for non-work turns goes to main flow.
   - `tool_call` → ensure an active work step exists (create one if starting fresh),
     append the call there.
   - `tool_result` → `updateToolResult` targets `activeStepEl`'s stack.
4. On step close: move `activeStepEl` from active zone to history zone, collapse it.
5. `loadMessages()`: group consecutive tool-using assistant turns into Work-block
   steps (collapsed), prose-only turns into main flow. Reuse `appendToolCall` /
   `updateToolResult`. Live == persisted.
6. CSS: `.work-block`, `.work-active`, `.work-history`, active-vs-collapsed states.

### Progress Tracker

- [x] Phase 1 done (inline per-call + one-line preview) — see above
- [x] Phase 2 design documented (this section)
- [x] Phase 2 implemented (work block, active/history zones, boundary detection)
- [x] Phase 2 browser smoke test (Chromium headless, real index.html + app.js):
      MID_ACTIVE=1 / MID_HISTORY=0 mid-run; FINAL_ACTIVE=0 / FINAL_HISTORY=2 after
      endRun; summary flushed to main flow (MAIN_FLOW_ASSISTANTS=1); per-step
      previews render (`read`→`shell`); hasFollowUpContent()=true. See
      /tmp/harness/{stub_inline.js,drive_inline.js,gen.py}.
- [x] Fixed hasFollowUpContent() selector (`.message.assistant` never matched — role
      lives in a child `.message-role`; work steps are nested in .work-block, not
       direct #messages children). Continue button now shows after done.
- [x] Phase 2 commit (4d26ccf)
- [x] Smoke test re-verified 2026-09-25: local Chromium headless AND Docker container
      (codeassist:ui-smoke, byte-identical app.js) both PASS identical assertions
      (MID_ACTIVE=1, FINAL_HISTORY=2, MAIN_FLOW_ASSISTANTS=1, HAS_FOLLOWUP=true).
- [ ] Commit + push the re-verification (plan-doc status update only; app.js/style.css
      unchanged since 4d26ccf/c9574ce).
