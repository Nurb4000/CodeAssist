# General Code Review — 2026-09-17

Audit of the `921381d` refactor fallout (REST split, package renames) and related subsystems,
looking for silent regressions (things that don't crash but misbehave).

**Policy (per user):** issues are only *documented* here until the review is complete. Nothing in
this file is fixed yet. Fixing starts after sign-off, one issue at a time.

Status legend: `OPEN` (documented, unfixed) · `CLEARED` (investigated, not a bug) · `DONE`.

---

## A. Permission / confirmation flow

### A1. `DONE` — no "allow this tool for the rest of the session" for generic tools

- The confirm dialog (`codeassist/static/app.js`) offers session-scope trust **only** for:
  - `write`/`edit` → "Trust all writes in workspace"
  - `shell` → "Trust all shell commands for this session"
- Every other tool (git push, apply_patch, custom tools, …) was one-time Allow / Deny; every
  repeat use re-prompted.
- Verified this was **not** a refactor regression: `git show 42b2946^:static/app.js` is byte-for-byte
  the same dialog as today (Phase C.3 built it).
- **Fixed (serverside `agent.py`):** module-level `SESSION_TOOL_TRUST` (session-id → set of tools),
  in-process only, same semantics as A2. `run()` binds each `confirm_id` to its tool name before
  waiting (`_confirm_tools`); `resolve_confirm(..., trust_tool=...)` records the tool for the session;
  `needs_confirmation` honors per-tool session trust (after legacy trust flags, before permission
  rules). `server.py` WS `confirm_response` reads `trust_tool` and passes it through; `reset_trust`
  clears per-tool trust too. "Remember permanently" is wired separately: the confirm dialog's
  "Always allow <tool> (remember permission)" checkbox sends `remember`, each `confirm_id` is bound
  to its tool + file_path server-side (`Agent._confirm_requests` / `get_confirm_context`), and the
  server persists the allow from that stored context (see `FUTURE_ENHANCEMENTS.md`).
- **Fix (client `app.js`):** the confirm dialog shows a generic "Trust this tool for this session"
  checkbox for any non-`write`/`edit`/`shell` tool and sends `trust_tool: true` on approve.
- Covered by `tests/test_trust_scope.py` (per-tool trust via `resolve_confirm`, isolation between
  sessions, and an agent-level WS-confirm flow test: pump + separate approver task resolving
  `trust_tool=True`, tool executes, loop continues, `needs_confirmation` then False).

### A2. `DONE` — "this session" trust was actually "this connection"

Trust is now keyed by **session id**, in-process only (`agent.py` `SESSION_TRUST`): `Agent.set_trust`
writes through to the store, a fresh Agent (i.e. a reconnected WS) is seeded from it, and the old
`agent.reset_trust()` call on every WS connect was removed. Semantics decided: survives reconnect,
isolated per session-id, ephemeral across server restarts, fresh ids start untrusted. Covered by
`tests/test_trust_scope.py` (persists across agents same session, isolated per id, reset clears).

### A3. `CLEARED` — permissions DB

`permission_manager.save_permission_choice(...)` and default agents/research agents load paths are
wrapped in try/except and degrade to sensible defaults; not a boot blocker.

---

## B. Session / message persistence

### B1. `DONE` — DB lived in the wrong place in Docker

Fixed earlier today (see `docs/RUNTIME_REVIEW_2026-09-17.md`, finding #11): DB now honors
`CODEASSIST_DATA_DIR`, compose points it at the `/app/data` volume, images are DB-free.

### B2. `DONE` — WS handler wrote orphan message rows for unknown session ids

`/ws/{session_id}` previously accepted any id and `agent.run()` inserted messages even when no
`sessions` row existed. Now the endpoint calls `Session.get_or_create()` (`session.py` — atomic
`INSERT OR IGNORE`, race-safe), so a stray client (or a reconnecting tab) materializes the session
row and messages are never orphaned. Test `test_ws_unknown_session_id_creates_session_not_orphan`
proves the row is created and the whole `messages` table has zero orphan rows after the flow.
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

### D2. `DONE` — reasoning models may return empty `content`

Audit result: the app has **no non-stream chat call sites** — chat, `llm_compact_messages` and
`_generate_llm_summary` all go through `LLMClient.stream()` (`stream: True`). The residual gap was
stream-side: a reasoning model can emit its reply as `delta.reasoning_content` with empty
`delta.content` (or exhaust `max_tokens` mid-thinking). Fixed in `llm.py stream()`: when
`delta.content` is empty it falls back to `reasoning_content` (guarded by `isinstance(str)`, safe
for non-reasoning providers). Tests: `test_stream_falls_back_to_reasoning_content`,
`test_stream_reasoning_attr_absent_yields_no_text`.

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

### F1. `DONE` — snapshot manager fails inside Docker

Root cause was environmental + one real bug:
1. **Docker "dubious ownership"** — when `WORKSPACE` mounted the repo itself, container-root git hit
   the host-user-owned `.codeassist/snapshot` and every `git` call exited 128. Fixed by registering
   the snapshot dir with `git config --global --add safe.directory` in `initialize()`.
2. **Latent initial-commit bug** — a fresh repo skipped its initial commit because the
   freshly-written `.gitignore` made `git status --porcelain` non-empty, leaving the repo without
   `HEAD` (so `git rev-parse HEAD` later exited 128). `initialize()` now checks `git rev-parse
   --verify HEAD` instead, seeding the commit when HEAD is absent.

Verified in a rebuilt container: log shows `Snapshot manager initialized` with no error, and the
snapshot repo has an `Initial snapshot` HEAD commit. Tests: `tests/test_snapshot.py`. (Feature kept
enabled in Docker — it now works.)

---

## G. Security

### G1. `CLEARED` — HTTP + WS auth are both present and consistent

HTTP paths (except `/health`, `/favicon.ico`, `/static/*`) are gated by an HTTP-Basic middleware
(`server.py:217-247`); WS uses a `sec-websocket-protocol` header handshake (`server.py:338-342`).
Both compare with `hmac.compare_digest`. REST routes don't each re-check auth but are covered by
the middleware. No CORS middleware — fine for the same-origin UI, but a separate frontend build
would need it.

### G2. `CLEARED` — `/api/config` does not leak secrets

Response is a fixed dict (model, provider, workspace, agent_name, vision, vision_capable, features) — no
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

## I. Registry feature surface (configs with API but no UI) — PARTIAL (agent switcher done)

The server exposes registries for **skills** (`/api/skills`), **agents** (`/api/agents`),
**MCP** (`/api/mcp/servers`), **LSP** (`/api/lsp/servers`), **plugins**, and **custom tools**
(`/api/tools/manage/scan`). The chat UI only linked to Tool Manager (`tools.html`) and KB
(`kb.html`); no UI existed for skills, agents, MCP, or LSP servers.

**Fixed — agent switcher (chat UI):** a dropdown in the chat sidebar (populated from
`/api/agents`, keyed by registry id) sends the existing `switch_agent` WS message. The server
now guards switching while a turn is streaming, rebuilds the agent's system prompt from the new
`AgentConfig`, and **persists the choice per session** (new `sessions.agent_name` column, schema
v7; `Session.get_agent_name`/`set_agent_name`). On every WS connect the server announces the
active agent (`active_agent`), so per-session choices survive reconnects and reloads.
`AgentConfig.to_dict()` added; `/api/agents` now returns `id` (registry key) alongside display
`name`. Covered by `tests/test_app_smoke.py::test_ws_agent_switcher` plus `test_session.py`
agent-selection tests.

**Fixed — registry admin page:** new `static/admin.html` + `admin.js` backed entirely by the
existing APIs — lists skills (create/reload), MCP servers (create/delete), LSP servers
(create/delete), plugins (read-only), custom tools (reload), and agents (read-only). Linked from
the chat sidebar header/footer. Covered by `test_static_admin_page_served`.

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
| 4 | S | **B2** — WS unknown session id | ✅ Done (`Session.get_or_create` at WS connect; test asserts zero orphan `messages` rows). |
| 5 | S-M | **A2** — trust scope | ✅ Done (session-keyed `SESSION_TRUST`; reconnect keeps trust; `reset_trust` on connect removed; `tests/test_trust_scope.py`). |
| 6 | M | **D2** — reasoning-model content | ✅ Done (audit: no non-stream call sites; `stream()` falls back to `reasoning_content`; 2 new stream tests). |
| 7 | M | **F1** — snapshot in Docker | ✅ Done (safe.directory + HEAD-based initial commit; verified in rebuilt container: clean log, HEAD commit exists; `tests/test_snapshot.py`). |
| 8 | M | **A1** — per-tool "trust for this session" | ✅ Done (`SESSION_TOOL_TRUST` + `_confirm_tools` binding in `agent.py`; generic trust checkbox + `trust_tool` in `app.js`/WS `confirm_response`; `needs_confirmation` honors it; `tests/test_trust_scope.py` incl. agent-level confirm-flow test). |
| 9 | L | **I** — headless registry UIs | ✅ Agent switcher (dropdown, streaming guard, per-session persistence via `sessions.agent_name` schema v7, `active_agent` on connect) + registry admin page (`static/admin.html`) for skills/MCP/LSP/plugins/custom-tools/agents; smoke + WS tests. |

After #9, revisit `FUTURE_ENHANCEMENTS.md` (in-app settings UI, KB Q→A, auto-titles, pin/archive)
as the next backlog.

## Status summary

- `DONE`: A1, A2, B1, B2, B4, D2, E1, E2, F1, H1
- `CLEARED`: A3, B3, C1, D1, G1, G2, H2, J1, K1
- `OPEN`: none — all review items are DONE or CLEARED (I is partially addressed: agent
  switcher + registry admin page shipped; the broader in-app settings UI remains tracked in
  `FUTURE_ENHANCEMENTS.md`).

## Revision history

- 2026-09-17: full sweep — permissions, persistence, boot/schema, LLM, static, snapshot, security,
  API surface, registry UIs, telemetry. No fixes applied per policy.