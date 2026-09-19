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

- **Collapsible sections.** ✅ Done (commit `ff46a05`). Each `<h2>` section on `admin.html` is now
  wrapped in a `.admin-section` with a chevron toggle and a live item-count badge (`Skills (16)`).
  Wrapping is done in JS (`initAdminSections`) so the existing `*-body` `getElementById` references
  in `admin.js` stay intact. Sections default **expanded** (content visible on load) and collapse
  state persists per-section in `localStorage`; the toggle re-checks on window resize.
- **Sidebar menu expands + navigates.** ✅ Done (`ff46a05`). Clicking a sidebar nav entry now
  expands the target section if collapsed, smooth-scrolls to it, and flashes/highlights the heading
  briefly so the landing is obvious (instead of a bare `jump to #anchor`).
- **Visible "Back to chat" control.** ✅ Done (`ff46a05`). Replaced the tiny `←` glyph in the admin
  sidebar header with a labeled "← Back to chat" button (inline SVG arrow + text).
- **Edit/remove for every item** — today only create (skills/MCP/LSP/agents) and delete
  (MCP/LSP + custom agents) are wired; built-in agents are protected. Add:
  - edit (rename / description / config JSON / enabled toggle) for registered items, backed by
    `PUT`/`PATCH` endpoints (agents already have `PATCH /api/agents/{key}`-style surface; skills,
    plugins, custom tools and their toggles need equivalent routes);
   - remove/disable for skills, plugins, and custom tools (with a delete confirmation), so admins
     aren't limited to "reload from disk" / read-only tables.
    - **Edit/remove wiring status:** MCP (`PUT /api/mcp/servers/{id}`), LSP
      (`PUT /api/lsp/servers/{id}`) and custom agents (`PATCH /api/agents/{id}`) now have edit
      endpoints + inline edit modals on `admin.html`; `MCPServer`/`LSPServer` gained `update()` and
      `set_enabled()` in `session.py`, and `AgentManager.update_agent` persists in-memory + DB.
      Skills/plugins/custom-tools remain disk-registry-backed (see below).
      - **GAP — DB MCP/LSP servers are not loaded into the running client.** ✅ CLOSED (boot-load +
        live reload). The admin page stores servers in the DB (`mcp_servers` / `lsp_servers`,
       `session.py`) and the
       edit endpoints persist there, but at boot `server.py::init_mcp()` only initialized
       `_config.mcp.servers` (the `[mcp].servers` block from `config.toml`) — it never read the DB
       rows into the live `mcp_client`.
     - **Done (boot-load, MCP + LSP):** admin-managed DB servers now load into the running client at
       boot. `server.py::_merged_mcp_servers()` unions config.toml MCP servers with DB-enabled
       servers (`enabled=1`, `MCPServer.list_all()`) before `mcp_client.initialize()`;
       `_start_lsp_servers()` does the same for LSP via `_merged_lsp_specs()` +
       `LSPServer.list_all()` → `lsp_client.start_server(...)`. config.toml wins on name collision;
       malformed DB rows (bad JSON, or an LSP command that won't launch) are skipped/logged with the
       per-server guard so one bad row can't abort boot. `init_mcp()` returns early when MCP is
       disabled; `_start_lsp_servers()` no-ops when LSP is disabled — so no DB read when a subsystem
       is off. Verified in-container: a seeded MCP `db-srv` logs `Connected to MCP server: db-srv`
       and a seeded LSP `db-lsp` logs `Started LSP server: db-lsp`; empty DBs start cleanly. Tests:
       `tests/test_registry_edit.py::TestMergedMCPServers`, `::TestMergedLSPSpecs`,
       `::TestStartLSPServers`.
      - **Done (live reload):** the `enabled` toggle and edit/delete endpoints now re-run the client
        reconcile on an admin change, so changes take effect without a server restart.
        `server.py::reload_mcp_servers()` calls `MCPClient.reload()` (adds new servers, drops removed
        ones, reconnects when a URL changes, prunes that server's tools) and
        `reload_lsp_servers()` calls `LSPClient.reload()` (starts new/changed specs, gracefully stops
        removed ones via `shutdown`+`exit`, leaves unchanged servers running). The three admin routes
        (`routes/mcp.py`, `routes/lsp.py`) call the matching reload after create/update/delete,
        guarded so a reload error never fails the edit. Verified in-container on a live app: POSTing a
        server logs `Connected to MCP server: live-srv` and deleting it logs `Disconnected MCP
        server: live-srv`, no restart. Tests: `TestMCPClientReload`, `TestLSPClientReload`,
        `TestReloadWiring`. Config.toml servers are unaffected throughout.
       - **Done (admin UI + off-request-path):** a **Reload connections** button now sits in the MCP
         and LSP section toolbars (`admin.html` `#mcp-reload` / `#lsp-reload`) and calls the new
         awaited endpoints `POST /api/mcp/reload` (returns `{ok, reconnected:[...]}`) and
         `POST /api/lsp/reload` (`{ok:true}`), wired in `admin.js` with a status toast. The
         create/update/delete routes no longer await the reconcile — they hand it to
         `server.spawn_reload()`, which runs it as a background `asyncio` task (retained in
         `_reload_tasks` until done, errors logged) so batch edits never hold the HTTP response open
         during reconnect. Verified in-container: `/api/mcp/reload` and `/api/lsp/reload` return 200,
         both buttons ship in served `admin.html`, and a POST to an unreachable server returns 200 in
         ~5ms (the failed reconnect is logged in the background, not on the request path). Tests:
         `TestReloadEndpoints`, plus `TestReloadWiring` drained via `_flush_reloads()`.

- **Cosmetic — hide/show left menu toggle.** ✅ Done. Both the chat sidebar (`index.html`) and the
  admin sidebar (`admin.html`) now have a show/hide toggle. A chevron **collapse** button lives in
  each sidebar header (points left, toward the pane); a fixed circular **reveal** button sits at the
  top-left of the page and appears only while the sidebar is hidden (points right). Toggling adds or
  removes `.sidebar-hidden` on `#app` (`#app.sidebar-hidden #sidebar { display: none }`), expanding
  the main content area. State persists in `localStorage` under `codeassist:sidebarVisible`
  (default visible); `Ctrl/Cmd+B` also toggles. Purely frontend — no backend change. Verified in a
  container: `/static/index.html` and `/static/admin.html` both serve the collapse + reveal buttons,
  and `style.css`/`app.js`/`admin.js` ship the `.sidebar-hidden` rule and `initSidebarToggle()`.

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

- **UI screenshot + vision-analysis loop (debugging/QA tool).** The project already has an
  `image_analyze` tool (sends an image file to a vision-capable LLM), but nothing can *take* a
  screenshot of the running UI. Add a **`screenshot` tool** that drives headless Chromium via the
  DevTools Protocol (CDP) to capture a page (by URL or the live app) and save a PNG, then optionally
  pipe it straight into `image_analyze`. Why: reliable UI verification without a human in front of a
  browser — the agent can capture the sidebar/header after a change, confirm layout/icons/rendering,
  and report regressions. Notes:
  - **Tool, not a skill.** It must execute an external program (chromium) and return a binary image,
    so it is a registered `Tool` (like `image_analyze`). A companion **skill** (`ui-debug.md`) can
    document the workflow — "change UI → screenshot → analyze vs. expected → fix" — but the skill
    alone cannot capture an image. So: tool required, skill optional for guidance.
  - **Docker deployments need Chromium in the image.** Headless Chrome is ~150MB+ with deps; make it
    an optional install (tool fails gracefully with a clear "chromium not found" error if absent) so
    minimal images don't pay the size cost. On the dev host, system Chrome/Chromium is enough — no
    extra libs required to drive it (CDP over a raw WebSocket + stdlib `http.client`/`socket`).
  - **Verification harness.** The same CDP approach can be used for automated click/DOM checks
    (dispatch events, assert modals render) as a `test`-adjacent tool, complementing the existing
    pytest suite.
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

## MCP test server (dev tooling)

- **Dummy MCP server for testing.** ✅ Implemented — see the sibling project
  `Python-MCP-test-server` (outside the CodeAssist repo, so it never ships in the image). Stdlib-only
  Python HTTP server speaking JSON-RPC 2.0; tools `echo`/`add`/`current_time` plus optional
  `resources`/`prompts`, a `ping`, and `GET /health`. Run `python selftest.py` to verify the contract.
  - **Transport is HTTP, not stdio.** CodeAssist's `MCPClient` (`codeassist/mcp_client.py`) connects
    to servers via an HTTP `url` and POSTs JSON-RPC — there is no stdio path today. Configure it with
    `[mcp.servers.test] url = "http://127.0.0.1:3001/mcp"`. (A stdio variant could be added later for
    other MCP clients, but it isn't what CodeAssist talks to.)
  - **Still open (future):** expand the tool set into a more realistic demo; verify the Admin page's
    enabled/disabled toggle against this live server end to end.

## Docker / ops

- Port `EXPOSE` already aligned to 8090; consider deriving `nginx`/reverse-proxy example. **[open]**
- **Health-check in compose.** ✅ Done (`docker-compose.yml`): added a `healthcheck:` calling
  `/health` (curl is in the slim image) with `interval 30s / retries 5 / start_period 15s`, so
  `docker compose up` reports container health instead of silently waiting. Targets port 8090;
  override `SERVER_PORT`/`[server] port` to match a custom port.
- **Fail fast when `config.toml` is missing.** ✅ Done (`docker-entrypoint.sh`): the entrypoint now
  prints a clear "config file not found … copy config.docker.toml" message and exits 1 instead of
  starting uvicorn and later failing with a confusing mount/port error. Verified in-container
  (exit 1 + message) and that the present-config path still extracts `[server] port` and proceeds.
  Full suite green (459 passed); image builds.