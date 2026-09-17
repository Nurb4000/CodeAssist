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

## Related "move internal" candidates

- **Workspace/session management UI**: deleting sessions works via REST but the admin/health
  dashboard could show DB stats, orphaned message rows, old data cleanup.
- **Admin web UI for the knowledge base**: entry editing, embedding rebuild, PII review already
  exist as endpoints (`/api/kb/*`); surface them behind an admin tab rather than raw JSON.
- **Tool trust management UI**: `/api/tools/manage/*` (trust, usage, scan) works; give it a real
  page instead of the raw console.
- **Health/status panel**: embed `/health`, LLM connectivity, DB path + size, and a "Restart
  needed" indicator when settings change.

## Robustness / correctness (found during the 2026-09-17 runtime review)

- **WebSocket with unknown session id**: `/ws/{session_id}` will happily write message rows whose
  parent session row doesn't exist. The UI always creates the session first, but the server should
  `get_or_create` (or 404) instead of silently writing orphan rows.
- **Reasoning models**: some models (e.g. Ornith 1.5) return the reply in
  `message.reasoning_content` with empty `content` on non-streamed calls. Streaming works; decide
  how to surface reasoning vs final answer consistently.
- **Session list rendering**: verify `app.js` handles the `sessions` table fields
  (`name`/`created_at`/`updated_at` — the API returns those; the UI reads `title` in places).
- **Legacy cruft**: `codeassist/test_*.py` still contain stale top-level imports; remove or port
  them to `tests/`.

## Docker / ops

- Port `EXPOSE` already aligned to 8090; consider deriving `nginx`/reverse-proxy example.
- Health-check in compose (`healthcheck:` calling `/health`).
- `docker compose up` should fail with a clear message when `config.toml` is missing (it's now
  correctly gitignored) instead of a confusing mount/port error.