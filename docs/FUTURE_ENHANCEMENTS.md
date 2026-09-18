# Future Enhancements

Backlog of improvements that don't belong in a one-off bugfix. Nothing here is scheduled; this
file exists so the ideas are captured.

## In-app settings / configuration UI (move config internal)

Currently all runtime configuration lives in `config.toml` (a local, gitignored file). That is
fine for power users but opaque for everyone else, and it's the source of a lot of the
"wrong thing in config" bugs.

- **Admin page/section with editable settings** for the most common knobs:
  - **LLM provider** — provider (openai / custom), `base_url`, `api_key` (stored masked),
    `model`, temperature, `context_window`, max tokens.
  - Live "Test connection" button that hits `/v1/models` (or the provider's equivalent) and shows
    the result.
  - Server port/host, workspace path (with restart note), agent default name.
  - Feature toggles already surfaced by `/api/config` (skills, plugins, MCP, LSP, git).
- Persist overrides (in the session DB or a dedicated `settings` table) layered on top of
  `config.toml`, so defaults stay in files and per-user values are internal.
- Flag on every setting: "file-managed" vs "UI-managed" to avoid confusion.
- (A first registry-only admin page — `static/admin.html` — already ships the skills/MCP/LSP
  plugin/custom-tools/agent browsing + skills/MCP/LSP create & MCP/LSP delete; fold it into the
  full settings UI rather than adding a separate "config" page.)

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

- **Tool permission "allow for rest of session"** — a previous version offered a permission choice
  that let the agent use an unapproved tool for the *whole session*; the current confirm dialog only
  offers session-scope trust for `write`/`edit` and `shell` (see `docs/CODE_REVIEW_2026-09-17.md`,
  A1/A2). The server's `remember` path is half-wired (`server.py:468-481`) but dead: the client
  never sends `tool`/`file_path`/`remember`, and `confirm_id` isn't bound to its tool server-side.
  To implement: track tool+args per `confirm_id`, add a per-tool "Trust for this session" checkbox,
  and keep "remember permanently" via `save_permission_choice`.

## Knowledge base

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