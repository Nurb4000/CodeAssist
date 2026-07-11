# CodeAssist - Development Progress

Track implementation progress across sessions. Update this file after each work session.

---

## Current State

**Last session:** 2026-07-11
**Current phase:** Phase 1 & 2 COMPLETE - Ready for testing
**Last thing worked on:** Full MVP implemented and smoke tested
**Blocking issues:** None

---

## Completed Work

| Date | What was done | Files | Notes |
|------|--------------|-------|-------|
| 2026-07-11 | Full MVP: config, LLM client, 8 tools, agent loop, server, web UI | ALL | Smoke test passed, all imports OK |

---

## Pending Work

### Phase 1: Core Agent (MVP)
- [x] Config loading (TOML)
- [x] LLM client with OpenAI-compatible streaming
- [x] Agent loop with tool calling
- [x] Tool registry + base class
- [x] 8 core tools: read, write, edit, shell, glob, grep, webfetch, todo
- [x] Session persistence (SQLite)
- [x] System prompt construction

### Phase 2: Web UI
- [x] FastAPI server with WebSocket
- [x] Chat UI (HTML/CSS/JS)
- [x] Streaming text display
- [x] Tool call/result visualization
- [x] Session list/management
- [x] Markdown rendering

### Phase 3: Polish
- [ ] Error handling and retries
- [ ] Configuration validation
- [ ] Model switching
- [ ] Context window management / compaction
- [ ] Rate limiting / retry on API errors
- [ ] Better file size limits in read tool

---

## Decisions & Deviations

| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-07-11 | Used `openai` SDK for LLM | Both OpenAI and llama.cpp expose `/v1/chat/completions` |
| 2026-07-11 | Included all 8 tools in MVP | Tools are simple enough, better UX out of the box |
| 2026-07-11 | Used `wcmatch` for glob | Supports `**/*` patterns natively in Python |
| 2026-07-11 | Vanilla JS for frontend | No build step, fast to iterate |

---

## Gotchas & Notes

- Requires Python 3.11+ (for `tomllib` in stdlib)
- Virtual env at `.venv/` - activate before running
- Set `OPENAI_API_KEY` env var or put key in `config.toml`
- For llama.cpp: set `base_url` in config.toml to `http://localhost:8080/v1`
- Server: `.venv/bin/python server.py` or `uvicorn server:app`

---

## File Inventory

| File | Purpose | Lines |
|------|---------|-------|
| `config.py` | TOML config loading | ~65 |
| `llm.py` | OpenAI-compatible streaming client | ~130 |
| `agent.py` | Core agent loop with tool calling | ~95 |
| `session.py` | SQLite session/message persistence | ~120 |
| `prompts.py` | System prompt construction | ~70 |
| `server.py` | FastAPI + WebSocket server | ~100 |
| `tools/__init__.py` | Tool base class + registry | ~65 |
| `tools/read.py` | Read file contents | ~55 |
| `tools/write.py` | Write file contents | ~30 |
| `tools/edit.py` | Surgical string replacement | ~70 |
| `tools/shell.py` | Shell command execution | ~65 |
| `tools/glob.py` | File pattern matching | ~50 |
| `tools/grep.py` | Content search (ripgrep/Python) | ~85 |
| `tools/webfetch.py` | URL fetching | ~80 |
| `tools/todo.py` | Task list management | ~70 |
| `static/index.html` | Chat UI | ~40 |
| `static/style.css` | Dark theme styling | ~270 |
| `static/app.js` | Frontend logic (WS, streaming) | ~270 |
