# Future Enhancements

Backlog of improvements that don't belong in a one-off bugfix. Nothing here is scheduled; this
file exists so the ideas are captured.

## In-app settings / configuration UI (move config internal)

Currently all runtime configuration lives in `config.toml` (a local, gitignored file). That is
fine for power users but opaque for everyone else, and it's the source of a lot of the
"wrong thing in config" bugs.

- ~~**First slice — admin Settings tab.**~~ ✅ Done (schema v9 `settings` table,
  `codeassist/settings.py`): a declarative `SETTINGS_CATALOG` + `SettingsStore` layers UI
  overrides on top of `config.toml` (applied at boot before subsystems init). `GET/PUT
  /api/settings`, `DELETE /api/settings/{key}` (secret values stored masked, never returned),
  and `POST /api/config/test-connection`. Admin page has a Settings tab (LLM, Server, Agent,
  Tools, Features groups) with per-key source badge ("overridden" vs "from config.toml"),
  restart-required flags, reset-per-key, and a "Test LLM connection" button.
- **Admin page/section with editable settings** for the most common knobs (remaining):
  - **LLM provider** — provider, `base_url`, `api_key` (masked), `model`, temperature,
    `context_window`, max tokens — present but extend with per-field validation + sync-back
    to `config.toml` for the container's mounted file.
  - **Server port/host/workspace** are catalog-backed but restart-required; consider a
    one-click "apply then restart" flow instead of just a notice.
  - Feature toggles already surfaced by `/api/config` (skills, plugins, MCP, LSP, git) —
    toggling still requires a restart (subsystems boot once); a live hot-reload for these is
    the bigger remaining win.
- Persistent overrides currently live ONLY in the DB (`config.toml` is mounted read-only in the
  container). Decide whether "UI-managed ≠ file-managed" should eventually write back a
  `config.overrides.toml` so overrides survive a data-dir reset.
- Flag on every setting: "file-managed" vs "UI-managed" to avoid confusion — ✅ done via the
  per-key source badge.
- (A first registry-only admin page — `static/admin.html` — already ships the skills/MCP/LSP
  plugin/custom-tools/agent browsing + skills/MCP/LSP create & MCP/LSP delete; fold it into the
  full settings UI rather than adding a separate "config" page.)

## Admin page UX

`static/admin.html` currently renders every registry as a flat `<h2>` section (skills, MCP, LSP,
plugins, custom tools, agents) with a sidebar nav of plain anchor links. Nice-to-haves:

- **Collapsible sections** — make skills/agents/custom-tools sections collapsible like the
  plugins/servers sections, defaulting to *collapsed* so the page stays compact. A per-section
  count badge (`Skills (12)`) on the toggle makes the collapsed state still useful.
- **Sidebar menu expands + navigates** — clicking a left-hand menu entry should do more than
  `jump to #anchor`: expand its section if collapsed, then scroll to it (and flash/highlight the
  section briefly so the landing is obvious).
- **Visible "Back to chat" control** — the current `←` glyph in the admin sidebar header
  (`.header-icon` link to `index.html`) is a tiny, easy-to-miss character. Replace with a
  prominent labeled button (e.g. "&larr; Back to chat") styled like a real action, so admins
  aren't hunting for the way back.
- **Edit/remove for every item** — today only create (skills/MCP/LSP/agents) and delete
  (MCP/LSP + custom agents) are wired; built-in agents are protected. Add:
  - edit (rename / description / config JSON / enabled toggle) for registered items, backed by
    `PUT`/`PATCH` endpoints (agents already have `PATCH /api/agents/{key}`-style surface; skills,
    plugins, custom tools and their toggles need equivalent routes);
  - remove/disable for skills, plugins, and custom tools (with a delete confirmation), so admins
    aren't limited to "reload from disk" / read-only tables.

## Related "move internal" candidates

- **Workspace/session management UI**: deleting sessions works via REST but the admin/health
  dashboard could show DB stats, orphaned message rows, old data cleanup.
- **Admin web UI for the knowledge base**: entry editing, embedding rebuild, PII review already
  exist as endpoints (`/api/kb/*`); surface them behind an admin tab rather than raw JSON.
- **Tool trust management UI**: `/api/tools/manage/*` (trust, usage, scan) works; give it a real
  page instead of the raw console.
- **Health/status panel**: embed `/health`, LLM connectivity, DB path + size, and a "Restart
  needed" indicator when settings change.

## Feature gaps (from the 2026-09-17 code review) worth adding

Gaps found during the review sweep that are missing *features*, not bugs:

- ~~**Auto-title sessions from the first message.**~~ ✅ Done (schema: no migration
  needed — `Session.add_message` replaces the untouched `%Y-%m-%d %H:%M` default name with a
  title derived from the first user message; helpers `is_default_title`/`derive_title` in
  `session.py`, 60-char word-boundary truncation).
- ~~**Agent switcher in the chat UI.**~~ ✅ Done (review item I): sidebar dropdown keyed by
  registry id, streaming guard, per-session persistence (`sessions.agent_name`, schema v7),
  `active_agent` on connect. Agent-management pane also done: `POST/DELETE /api/agents`, admin
  agents tab now creates agents and deletes custom ones (built-ins report `builtin: true` and
  are protected server-side via `BUILTIN_AGENT_KEYS`).
- ~~**Session pin/star/archive.**~~ ✅ Done (schema v8): `sessions.is_pinned` column,
  `PATCH /api/sessions/{id}` `{"pinned": bool}`, `Session.set_pinned`, list ordered
  `is_pinned DESC, updated_at DESC`; sidebar star toggle.
- ~~**Expose session summaries in the chat UI.**~~ ✅ Done: `Session.list_all` LEFT JOINs
  `session_summaries`, sidebar shows a truncated summary line under each session name
  (tooltip with full text).
- **Pin/archive depth beyond the boolean** (starred folders, archive vs pin semantics) remains
  an idea if wanted later.

## Robustness / correctness (found during the runtime review)

- ~~**Tool permission "allow for rest of session".**~~ ✅ Done: session-scope trust for `write`/`edit`
  and `shell`, per-tool session trust (A1), and **"remember permanently"** via `save_permission_choice`.
  The confirm dialog's "Always allow <tool> (remember permission)" checkbox sends `remember: true`;
  the server binds each `confirm_id` to its tool + file_path at request time
  (`Agent._confirm_requests` / `get_confirm_context`) and persists the allow from that
  server-side context, never from client-echoed values (`server.py` `confirm_response`).

## Chat UX / telemetry

- **Collapsible "thinking" block.** ✅ Done (commit `f47abb8`). Reasoning content is now emitted as
  a separate `ReasoningDelta` (`llm.py`), forwarded as a WS `reasoning` event and accumulated in
  `agent.py`, then persisted in the new nullable `messages.reasoning_content` column (schema v10,
  `session.py`). The chat UI renders per-message collapsible `<details>` "Thinking" blocks
  (`app.js` `appendReasoningToCurrent` / `applyThinkingVisibility`), defaulting to collapsed with a
  global Show/Hide toggle persisted in `localStorage`. Verified live against the Ornith backend:
  reasoning deltas stream separately from the answer.

- **Export / import a chat.** ✅ Done (this batch). Backend export/import endpoints already existed;
  the gap was the chat UI and a round-trip bug. Added:
  - Export button on each sidebar session item (`loadSessions`) → `openExportDialog` with a
    "Redact PII" checkbox; downloads a self-contained JSON bundle via `downloadJSON`.
  - Import button in the sidebar header (`#import-btn`) → `openImportDialog`; accepts a `.json`
    file (auto-reads into a paste area) or pasted JSON, POSTs `/api/sessions/import`, then
    `switchSession` to the newly created session. Backdrop-click / Escape / close-button dismiss.
  - `session.add_message` now accepts/persists `reasoning_content`; `SessionManager.import_session`
    forwards it (and tolerates already-parsed `tool_calls`), so thinking blocks survive export→import.
  - Transient `showStatus` toast for success/failure feedback.
   Tests: `test_export_import_preserves_reasoning_content` (data layer) and REST round-trip in
   `test_app_smoke.py::test_session_lifecycle`. Verified end-to-end through the live container:
   exported `reasoning_content` reappears verbatim on the imported session.
   - **Icon polish (follow-up).** Session/header buttons previously used emoji (`&#128451;`,
     `&#128454;`) which render as tofu squares in this environment — the export button was
     effectively invisible and the import button looked like a blank square. Replaced all session
     + header action icons with inline SVG (`ICONS` in `app.js`, `currentColor` so they inherit
     hover color). Grouped header actions under `.header-actions` so the import button no longer
     overhangs the sidebar edge and widened the sidebar `260px → 340px` so names/buttons no longer
     clip. Rename button now a clear pencil SVG (still hover-revealed with pin/delete). Verified via
     CDP that clicking import/export/new-session all work (`MODAL_OPEN` / session count grows), with
     no load-time JS exceptions.
   - **Stale-cache after rebuild (root cause of "half-applied" UI).** Browsers fell back to
     heuristic caching and served a stale `app.js`/`style.css` after a container rebuild, so fresh
     icons/layout appeared half-done even on a clean load. Added a `static_cache_headers` middleware
     in `server.py` that sets `Cache-Control: no-cache, no-store, must-revalidate` (+ Pragma/Expires)
     on every `/static/*` response and dropped the useless static `?v=dev` query strings from
     `index.html`. Also converted the header **nav** icons (`🔧⚙️📚`) to SVG — those emoji rendered at
     different sizes in Firefox vs Chrome and wrapped the header onto multiple lines (pushing
     import/new-session off-row). Sidebar now stays a single row across browsers.
   - **Icon polish (follow-up).** Session/header buttons previously used emoji (`&#128451;`,
     `&#128454;`) which render as tofu squares in this environment — the export button was
     effectively invisible and the import button looked like a blank square. Replaced all session
     + header action icons with inline SVG (`ICONS` in `app.js`, `currentColor` so they inherit
     hover color). Sidebar widened `260px → 320px` and header actions grouped under `.header-actions`
     so the import button no longer overhangs the menu edge. Rename button now a clear pencil SVG
     (still hover-revealed with pin/delete). Verified via CDP that clicking import/export both open
     their modals (`MODAL_OPEN`), and the export dialog contains the redact checkbox + download btn.

- **Running token count + token rate.** ✅ Done (this batch). The sidebar footer now shows a
  `#token-info` line: cumulative session tokens + live `tok/s` rate. Tokens are accumulated on the
  WS `finish` event (`data.usage.prompt_tokens`/`completion_tokens`) — no new endpoint needed;
  rate is computed between consecutive finishes. (Per-model totals on the KB/tool dashboard still
  open.)

- **Model name in the sidebar footer.** ✅ Done. `#model-info` now renders the **effective**
  model (`configData.effective_model`, which is the auto-detected local model or the configured
  one for external providers) plus its context window, with a `• auto` badge when detected from a
  local backend.

- **Auto-detect local model + context window.** ✅ Done (this batch). `capabilities.get_backend_info()`
  now probes `/v1/models` (cached 60s, fail-closed) for the model id and context window in addition
  to vision. `/api/config` returns `detected_model`, `effective_model`, `effective_context_window`,
  `detected_context_window`, `backend_source`, `backend_external`. For **local** backends the
  effective model/context come from the probe; for **external** providers the admin-configured
  values are kept and `backend_source` is null (no "auto" badge). llama.cpp's `/v1/models` returns
  only the model id (no context metadata), so the context window falls back to the configured
  value — verified live against the Ornith backend. Remaining: auto-detect context window when a
  backend actually exposes it (e.g. via a props/metadata endpoint).

## Knowledge base

- **Review the KB process end-to-end and harden it** — the pipeline currently writes *everything*
  it heuristically matches and there's no real garbage control:
  - **Extraction audit.** Seven extractors (`_extract_from_*` in `session_hook.py`) insert with
    hard-coded confidences (0.4–0.7); the `_classify_and_create_knowledge` keyword indicators and
    the "User request pattern" items (0.5) are the biggest junk source. Review each extractor:
    tighten the indicators, drop or re-score low-value patterns, and stop logging one-off session
    trivia as durable knowledge.
  - **Automated article review / garbage collection.** Beyond the crude dedup today
    (≥80% char overlap on the first 200 chars + per-session MD5 in `_create_knowledge_if_new`):
    - add an embedding-similarity near-duplicate check before insert (cosine over the vector store,
      not character overlap) and merge/update rather than duplicate;
    - a background review pass that re-scores low-confidence and never-used entries and moves junk
      to a soft-deleted/archived set (restorable), instead of leaving 0.4–0.5 clutter forever;
    - a quality gate that flags or drops entries with no usable content, PII/secrets, or
      < min active usage;
    - retire or promote by `usage_count`: frequently-retrieved entries can be promoted (→ skills),
      stale entries age out. (A repetitive-pattern → skill suggestion already exists at
      `session_hook.py:865`; formalize it.)
  - **Review UX.** Surface the existing `/api/kb/*` endpoints behind an admin "Knowledge base"
    tab with review actions (verify / flag / archive / delete), a stats bar (entries by type and
    confidence, orphan rows, near-duplicate clusters, usage distribution), and batch re-embed.
  - The Q→A pairing item below is part of this: answers (and outcomes) are never captured today, so
    the review should include "what was asked vs what actually worked."

- **Track Q→A pairs, not just prompts** — knowledge extraction currently stores the *user's
  question alone* (`_extract_from_user_questions` in `session_hook.py`, "User request pattern:
  <prompt>" with confidence 0.5). The assistant's answer is never captured or linked, so the KB
  records "what was asked" but not "what the answer/outcome was", which is what makes it useful.
  Plan: when a question gets an assistant reply, persist a Q→A knowledge entry (question, answer,
  tools used, file references, confidence from the reply), and let KB search return both sides of
  the exchange. Also consider indexing the *content* of answers (code snippets, fixes, decisions)
  rather than only the prompt.

- **WebSocket with unknown session id**: `/ws/{session_id}` will happily write message rows whose
  parent session row doesn't exist. The UI always creates the session first, but the server should
  `get_or_create` (or 404) instead of silently writing orphan rows.
- **Reasoning models**: some models (e.g. Ornith 1.5) return the reply in
  `message.reasoning_content` with empty `content` on non-streamed calls. Streaming works; decide
  how to surface reasoning vs final answer consistently.
- **Session list rendering**: verify `app.js` handles the `sessions` table fields
  (`name`/`created_at`/`updated_at` — the API returns those; the UI reads `title` in places).
- **Legacy cruft**: `codeassist/test_*.py` were removed (see `CODE_REVIEW_2026-09-17.md` B4). ✅

## Chat file uploads

**Done.** Attach button is always visible and no longer gated on `vision`:
- Attach button always visible; images (png/jpeg/webp/gif) attach only when the model is
  vision-capable, with a clear "model doesn't support images" message otherwise.
- Any text file (by `text/*` MIME or common extension) can be attached and is inlined into
  the prompt as a `[Attached file: name]` text part ("Inline small files" behavior).
- Vision capability is auto-detected from the backend `/v1/models` `capabilities`
  (cached 60s, see `codeassist/capabilities.py`); `[llm] vision` remains a manual override
  (override OR auto-detect). Unknown/unreachable backend → not vision-capable (fail closed).
- `/api/config` exposes `vision_capable` to the client.
- Rate/size caps: max 4 images × 8MB; max 5 text files × 256KB (server + client enforced).
- Still open (later work): link/large-file streaming instead of inlining; upload progress UI.

Remaining backlog here: line 1 list from the "upload behavior" deferral (already handled by the
above detection), plus any GUI-managed toggles to land with the in-app settings UI.

## Docker / ops

- Port `EXPOSE` already aligned to 8090; consider deriving `nginx`/reverse-proxy example.
- Health-check in compose (`healthcheck:` calling `/health`).
- `docker compose up` should fail with a clear message when `config.toml` is missing (it's now
  correctly gitignored) instead of a confusing mount/port error.