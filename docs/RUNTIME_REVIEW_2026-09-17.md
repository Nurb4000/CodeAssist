# CodeAssist Docker / Runtime Review — 2026-09-17

Scope: after the `server.py` → `codeassist/server.py` refactor (commit `921381d`, and subsequent
Phase A–D work), the Docker image built but the running service was unusable. This documents every
breakage found (with evidence) and the fix applied to each.

## Headline

- **All findings below are resolved and verified** as of this date. The container builds, boots,
  and serves both the REST API and the full frontend (chat UI, Tool Manager, Knowledge Base).
- Full test suite passes: **382 passed** (includes a new app-boot REST smoke test).

---

## Findings and resolutions

### 1. FIXED — `docker-entrypoint.sh` imported a deleted module

`docker-entrypoint.sh` ran `uvicorn server:app`, but the top-level `server` module no longer exists
(it became `codeassist/server.py`). Uvicorn exited with `Error loading ASGI app. Could not import
module "server".`

**Fix:** `exec python -m uvicorn codeassist.server:app ...` (docker-entrypoint.sh:18).

### 2. FIXED — port mismatch; nothing reachable on any host port

`docker-compose.yml` publishes `8090:8090`; the entrypoint reads the port from the mounted
`config.toml`, which still said `port = 8000`, so the app bound an unpublished port.

**Fix:** the local (gitignored, untracked) `config.toml` `[server]` `port = 8000` → `8090` to match
compose and the `config.docker.toml` template. `config.toml` is a per-user local file and is **not
tracked in git** — as of this review it was removed from the index (see below). The port fix
therefore lives in the user's local copy, created via `cp config.docker.toml config.toml`.
Verified: `docker ps` shows `0.0.0.0:8090->8090/tcp` and `Uvicorn running on http://0.0.0.0:8090`.

### 3. FIXED — the whole REST API was missing

`register_routes(app)` was never called, and `routes/__init__.py` used a broken top-level import
(`from routes.sessions import router`). Every `/api/*` route 404'd.

**Fix:** added `from .routes import register_routes` + `register_routes(app)` in
`codeassist/server.py`; changed `routes/__init__.py` to relative imports (`.sessions`, etc.).

### 4. FIXED — route handlers used pre-refactor lazy imports

`routes/*.py` and `session_hook.py:988` lazily imported `session`, `session_manager`, `knowledge`,
`config`, `skills`, `custom_tools_loader`, `agents`, `embeddings` at top-level → module not found
at request time (500).

**Fix:** rewrote ~79 imports to `from codeassist.<module> import ...`. Verified symbols exist.

### 5. FIXED — method name drift inside route handlers

- `routes/sessions.py` forked via `Session.fork_session(...)` (didn't exist) → `Session(session_id).fork(name)`.
- `export_session` crashed with `TypeError: 'NoneType' object is not subscriptable` when `summary`
  had no `first_message` → `((summary or {}).get("first_message") or "Untitled")[:80]`.
- `routes/tools.py` `GET /manage/usage` was shadowed by `GET /manage/{tool_name}` (404) → moved
  before the parameterized route and removed the duplicate handler.

### 6. FIXED — test suite gave false confidence

No test booted the app or exercised the REST surface. Added `tests/test_app_smoke.py` which boots
`codeassist.server.app` with a `TestClient` against an isolated temp workspace + test DB and
asserts: the REST surface is registered (no 404/500 across 24 GET endpoints), `/health`, a full
session lifecycle (create/messages/fork/patch/tags/undo/export), and that
`/api/tools/manage/usage`, `/manage/scan`, `/tools/reload` are not shadowed. The fixture restores
the module globals the app lifespan mutates so other tests stay isolated.

### 7. FIXED — config drift & tracked secrets boundary

`config.toml` (mounted by compose) was tracked in git despite `.gitignore`, and is the dev config.
It may contain a real `api_key`, so it must stay local. **Fix:** removed from the git index
(`git rm --cached config.toml`, kept on disk), stays gitignored. Port aligned to 8090 as above.
Note: `config.docker.toml` was restored to its committed baseline — earlier local edits
(`base_url = "http://10.0.1.27:8080"`, `context_window = 700000`) were **dropped** to avoid
committing a private LAN IP; re-add locally if still wanted.

### 8. RESOLVED (operational) — `WORKSPACE` pointed at a non-existent directory

`WORKSPACE=~/home/ziggy/...` expands to `/home/ziggy/home/ziggy/...`, which Docker auto-created
empty (root-owned). **Fix:** use the real path, e.g.
`WORKSPACE=/home/ziggy/Code-Projects/CodeAssist docker compose up`. Orphaned root-owned dirs under
`/home/ziggy/home` were left in place (outside the repo).

### 9. FIXED — frontend assets were stranded (found during post-fix UI verification)

The refactor moved only an **empty stub** `codeassist/static/index.html`; the real frontend
(index.html, app.js, style.css, tools.html/js/css, kb.html/js/css, vendor) stranded at the repo-root
`static/` was not served (server mounts `codeassist/static` only). The chat UI and both GUIs loaded
empty pages.

**Fix:** relocated all assets into `codeassist/static/`, deleted the orphaned root `static/`,
updated path references in README/DESIGN/CONTRIBUTING. Verified all 13 static paths return 200.

### 10. DONE (awareness) — minor / hygiene

- `.dockerignore` excludes `*.md`, `tests/`, `data/` (intentional; docs/tests absent from image).
- Legacy `codeassist/test_*.py` files still carry stale top-level imports but are not collected by
  pytest (which uses `tests/`); left as known cruft for a later cleanup.

### 11. FIXED — SQLite DB lived in the wrong place → session history lost/revived

The DB is opened at `codeassist/data/codeassist.db` (`Path(__file__).parent / "data"`), i.e.
`/app/codeassist/data/` in the container. The compose persistence volume mounts at **`/app/data`**,
so it was never used, and the DB lived in the container's writable layer. Additionally the image
was *baking in* `codeassist/data/*.db-wal` / `*.db-shm` (the `.dockerignore` `*.db` rule doesn't
match the WAL/SHM sidecars), so every fresh container resurrected the same stale session list;
deletes only touched the ephemeral layer and thus "returned after restart".

**Fix:**
- `codeassist/session.py`: `DB_PATH` now honors `CODEASSIST_DATA_DIR` (default unchanged for local
  runs).
- `docker-compose.yml`: sets `CODEASSIST_DATA_DIR=/app/data`, pointing the DB at the
  `codeassist-data` volume.
- `Dockerfile`: `RUN rm -rf /app/codeassist/data` so images are always DB-free; `.dockerignore`
  adds `codeassist/data/`, `*.db-wal`, `*.db-shm`, `*.db-journal`.

Verified: fresh container starts with an empty session list; a chat persists across a full
`docker compose down && up` (same DB file in the volume, same session id + messages).

---

## Final verified state

```
$ docker compose build   # OK
$ docker compose up -d
INFO:  Application startup complete.
INFO:  Uvicorn running on http://0.0.0.0:8090
$ curl localhost:8090/health   # {"status":"ok","model":"gpt-4o","workspace":"/workspace"}
$ curl localhost:8090/api/config              # 200
$ curl localhost:8090/api/kb/stats            # 200
$ curl localhost:8090/api/tools/manage/list   # 200
$ curl localhost:8090/static/tools.html       # 200 (was 404)
$ curl localhost:8090/static/kb.html          # 200 (was 404)
$ python -m pytest tests/ -q                  # 382 passed
```

## Open items for a broader review (next pass)

The `921381d` refactor scattered a lot of behavior; this pass fixed the boot-blocking and
user-visible breaks. A follow-up structural review should sweep for silent regressions that don't
panic but misbehave: WebSocket handler writes message rows without a parent-session row when handed
an unknown session id (UI always creates the session first, so the normal flow is fine); session
list/title rendering in `app.js` vs the `sessions` table fields (`name`/`updated_at`); reasoning
-model replies emitted via `reasoning_content` in non-stream mode (streaming is fine); and legacy
`codeassist/test_*.py` cruft.