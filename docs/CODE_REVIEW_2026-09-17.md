# General Code Review — 2026-09-17

Audit of the `921381d` refactor fallout (REST split, package renames) and related subsystems,
looking for silent regressions (things that don't crash but misbehave).

**Policy (per user):** issues are only *documented* here until the review is complete. Nothing in
this file is fixed yet. Fixing starts after sign-off, one issue at a time.

Status legend: `OPEN` (documented, unfixed) · `CLEARED` (investigated, not a bug) · `DONE`.

---

## A. Permission / confirmation flow

### A1. `OPEN` — no "allow this tool for the rest of the session" for generic tools

- The confirm dialog (`codeassist/static/app.js`) offers session-scope trust **only** for:
  - `write`/`edit` → "Trust all writes in workspace"
  - `shell` → "Trust all shell commands for this session"
- Every other tool (git push, apply_patch, custom tools, …) is one-time Allow / Deny; every
  repeat use re-prompts.
- Verified this is **not** a refactor regression: `git show 42b2946^:static/app.js` is byte-for-byte
  the same dialog as today (Phase C.3 built it).
- The server already has half-built support: WS `confirm_response` accepts
  `remember`/`tool`/`file_path` and calls `agent.save_permission(...)` (`server.py:468-481`), but
  (a) the client never sends those fields, and (b) the server doesn't track which tool a given
  `confirm_id` belongs to, so the "remember" path is dead code.
- **Action (post-review):** track tool+args per `confirm_id`; add a "Trust for this session"
  checkbox for all tools (in-memory per connection) and a separate "Remember permanently"
  (persist via `save_permission_choice`, which already exists). Extend
  `needs_confirmation` to honor per-tool session trust. Already logged in
  `docs/FUTURE_ENHANCEMENTS.md`.

### A2. `OPEN` — "this session" trust is actually "this connection"

`Agent.reset_trust()` runs on every WS connect, so "trust for this session" lasts only as long as
the WebSocket stays open; reconnecting (or page refresh) resets it. If the intent is per-session-ID
(or per-workspace-day) persistence, the trust flags should key off the session id / a cookie, not
the connection.

### A3. `CLEARED` — permissions DB

`permission_manager.save_permission_choice(...)` and default agents/research agents load paths are
wrapped in try/except and degrade to sensible defaults; not a boot blocker.

---

## B. Session / message persistence

### B1. `DONE` — DB lived in the wrong place in Docker

Fixed earlier today (see `docs/RUNTIME_REVIEW_2026-09-17.md`, finding #11): DB now honors
`CODEASSIST_DATA_DIR`, compose points it at the `/app/data` volume, images are DB-free.

### B2. `OPEN` — WS handler writes orphan message rows for unknown session ids

`/ws/{session_id}` accepts any id; `agent.run()` inserts messages even when no `sessions` row
exists. UI always creates the session first, but a stray client (or a session deleted while a WS
was open) leaves orphaned `messages` rows referencing a dead session. **Action:** `get_or_create`
(or 404 + close code) at WS connect; garbage it in the process of a later cleanup sweep if ever
needed.

### B3. `CLEARED` — session list vs UI fields

`Session.list_all()` returns `sessions` columns (`id`, `name`, `created_at`, `updated_at`, …) and
`app.js` renders `s.name || 'Untitled'` (`app.js:144`). No field-name mismatch.

### B4. `DONE` — legacy `codeassist/test_*.py` cruft

Deleted (`test_api.py`, `test_knowledge_base.py`, `test_migration.py`, `test_self_creation.py`);
nothing imported them and `pytest tests/` stays green (382 passed).

---

## C. Boot / DB schema

### C1. `CLEARED` — "no such table: agents" in test logs

Fresh-DB migrations (v1→v6) all run from `init_db()`, called at app boot (`server.py:190`), so a
real server gets every table (agents, knowledge_entries, todos, FTS5 views, …). The log noise comes
from the test harness resetting the DB without running `init_db`. **Optional improvement:** have the
test `clean_database` fixture run `init_db()` for fidelity.

---

## D. LLM / agent runtime

### D1. `CLEARED` — connectivity to `10.0.1.27:8080`

Verified from the running container: `/v1/models` 200 and a full WS round-trip returns a reply.
`config.toml` `base_url` is honored end-to-end (`config.py:152` → `llm.py:57`).

### D2. `OPEN` — reasoning models may return empty `content` on non-stream calls

The Ornith 1.5 model put its answer in `message.reasoning_content` with `content=""` for
non-streamed requests. Streaming (`LLMClient.stream`) works, but any non-stream callers
(session title/summary generation, compaction) may persist empty text. Audit non-stream call sites.

---

## E. Static / frontend

### E1. `DONE` — assets stranded at old `static/`

Fixed earlier today (relocated into `codeassist/static/`; see runtime review #9).

### E2. `DONE` — `.dockerignore` / image hygiene

Built a clean image (`codeassist-e2-check`) from a working tree that *contains* live
`codeassist/data/codeassist.db{,-wal,-shm}` on the host. Verification image file search:
`find /app -name '*.db*'` → empty, and a whole-image `find / -name '*.db*'` (excluding system)
→ empty. `.dockerignore` (`codeassist/data/`, `*.db-wal/-shm/-journal`) + Dockerfile
`RUN rm -rf /app/codeassist/data` correctly keep DB/sidecar files out of the image.

---

## F. Runtime noise (non-fatal)

### F1. `OPEN` — snapshot manager fails inside Docker

Container logs show `Failed to initialize snapshot manager: Command '['git', 'add', '.']' returned
non-zero exit status 128`. The snapshot repo lives under the *mounted workspace*
(`/workspace/.codeassist/snapshot`), so container-side git operations run against the user's real
repo. Non-fatal (boot completes) but it spams logs and the snapshot feature won't work in Docker.
**Action:** check whether snapshot init should run in the container at all, or run against a
container-internal copy; confirm `git` identity/ownership inside the volume.

---

## G. Security

### G1. `CLEARED` — HTTP + WS auth are both present and consistent

HTTP paths (except `/health`, `/favicon.ico`, `/static/*`) are gated by an HTTP-Basic middleware
(`server.py:217-247`); WS uses a `sec-websocket-protocol` header handshake (`server.py:338-342`).
Both compare with `hmac.compare_digest`. REST routes don't each re-check auth but are covered by
the middleware. No CORS middleware — fine for the same-origin UI, but a separate frontend build
would need it.

### G2. `CLEARED` — `/api/config` does not leak secrets

Response is a fixed dict (model, provider, workspace, agent_name, vision, features) — no
`base_url`/`api_key`. (`routes/config.py`)

---

## H. API surface / duplication

### H1. `DONE` — `/analytics/tools` and `/analytics/llm` were implemented twice

`routes/kb_gui.py` (/api/kb) is now canonical and accepts the full filter set (`session_id`,
`tool_name`/`model`, `period_days`); `routes/tools.py` registers the same handlers as aliases via
`router.add_api_route`. Parity locked by `tests/test_app_smoke.py::test_analytics_parity_between_tools_and_kb_prefixes`.

### H2. `CLEARED` — frontend API maps match routes 1:1

`kb.js` (17 endpoints: stats, entries±CRUD, search, sessions, analytics, pii, settings, export,
import, clear) all exist in `routes/kb_gui.py`; `tools.js` (`/api/tools/manage/*`) all exist in
`routes/tools.py`. Smoke test also covers the main surface.

---

## I. Registry feature surface (configs with API but no UI)

The server exposes registries for **skills** (`/api/skills`), **agents** (`/api/agents`),
**MCP** (`/api/mcp/servers`), **LSP** (`/api/lsp/servers`), **plugins**, and **custom tools**
(`/api/tools/manage/scan`), but the chat UI only links to Tool Manager (`tools.html`) and KB
(`kb.html`). No UI exists for skills, agents (no agent switcher in `app.js`), MCP, or LSP servers.
Consolidate these into the settings/admin page (see `FUTURE_ENHANCEMENTS.md`); until then the APIs
are effectively headless.

---

## J. Cost / telemetry

### J1. `CLEARED` — usage tracking exists but is only surfaced in the KB GUI

`KnowledgeBase.log_llm_usage`/`log_tool_execution` + `/api/kb/analytics/*` exist. No per-session
"cost this session" indicator in the chat UI (only the KB analytics tab shows aggregate stats).

---

## K. Boot ordering / registry initialization

### K1. `CLEARED` — subsystem boot is deterministic

`server.py:119-158` initializes trust registry, tools (with dynamic reload), skills, plugins, MCP,
LSP, and the session hook is invoked at WS close (`server.py:543-544`). MCP/plugins default to
disabled. The only boot log noise is the snapshot manager failure (see F1).

---

## Action plan (effort-ordered — work top to bottom)

Rough sizing: **S** ≈ under an hour, **M** ≈ half a day, **L** ≈ a day+. Each item has a concrete
definition of done. Fix one at a time, test, commit — no overlapping changes.

| # | Effort | Item | Definition of done |
|---|--------|------|--------------------|
| 1 | S | **B4** — delete legacy `codeassist/test_*.py` | ✅ Done (4 files removed, 382 tests pass). |
| 2 | S | **E2** — finalize image DB hygiene | ✅ Done (image verified: zero `*.db*` files; separate build used, running container untouched). |
| 3 | S | **H1** — de-duplicate `/analytics/*` | ✅ Done (kb handlers canonical + richer filters; tools routes alias them; parity test `test_analytics_parity_between_tools_and_kb_prefixes`). |
| 4 | S | **B2** — WS unknown session id | `/ws/{id}` does `get_or_create` (or 404+close 1008) for missing sessions; test proves no orphan `messages` rows. |
| 5 | S-M | **A2** — trust scope | Decide semantics (session-id scoped vs connection scoped); store trust flags keyed by session id so reconnect preserves "trust for this session"; test. |
| 6 | M | **D2** — reasoning-model content | Audit every non-stream `chat.completions` call site (title/summary/compaction); fall back to `reasoning_content` when `content` empty; test with a reasoning model. |
| 7 | M | **F1** — snapshot in Docker | Snapshot init no longer errors in the container (skip in-container, or run against an internal copy w/ correct git identity); confirm logs are clean and feature still works non-Docker. |
| 8 | M | **A1** — per-tool "trust for this session" | Track tool+args per `confirm_id`; add a "Trust for this session" checkbox for all tools; honor it in `needs_confirmation`; (kept separate: "remember permanently" via `save_permission_choice`); tests for the WS confirm flow. |
| 9 | L | **I** — headless registry UIs | Ship an agent switcher (dropdown per session) first; then a settings/admin page for skills/MCP/LSP backed by the existing APIs; cover with smoke tests. |

After #9, revisit `FUTURE_ENHANCEMENTS.md` (in-app settings UI, KB Q→A, auto-titles, pin/archive)
as the next backlog.

## Status summary

- `DONE`: B1, B4, E1, E2, H1
- `CLEARED`: A3, B3, C1, D1, G1, G2, H2, J1, K1
- `OPEN` (fix after review sign-off): A1, A2, B2, D2, F1, and the registry-UI gap (I)

## Revision history

- 2026-09-17: full sweep — permissions, persistence, boot/schema, LLM, static, snapshot, security,
  API surface, registry UIs, telemetry. No fixes applied per policy.