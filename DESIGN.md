# CodeAssist - Design Plan

A Python web app reimplementation of [opencode](https://github.com/anomalyco/opencode)'s core functionality, connecting to OpenAI-compatible endpoints (OpenAI API, llama.cpp, or any `/v1/chat/completions` server).

---

## Architecture Overview

```
┌─────────────────────────────────────────────────┐
│                   Frontend                       │
│         Simple HTML + vanilla JS                 │
│    (chat UI, streaming display, tool output)     │
└────────────────────┬────────────────────────────┘
                     │ WebSocket + REST
┌────────────────────▼────────────────────────────┐
│                FastAPI Server                     │
│  ┌──────────┐  ┌───────────┐  ┌──────────────┐ │
│  │  Routes   │  │  Agent    │  │   Session    │ │
│  │  (REST +  │  │  Loop     │  │   Manager    │ │
│  │  WS)      │  │  (core)   │  │   (SQLite)   │ │
│  └──────────┘  └─────┬─────┘  └──────────────┘ │
│                      │                           │
│  ┌───────────────────▼───────────────────────┐  │
│  │              Tool Registry                 │  │
│  │  read │ write │ edit │ shell │ grep │ ...  │  │
│  └───────────────────────────────────────────┘  │
└────────────────────┬────────────────────────────┘
                     │ HTTP (OpenAI-compatible)
┌────────────────────▼────────────────────────────┐
│           LLM Endpoint                           │
│   OpenAI API  │  llama.cpp server  │  vLLM      │
│   POST /v1/chat/completions (streaming)          │
└─────────────────────────────────────────────────┘
```

---

## Tech Stack

| Component | Choice | Rationale |
|-----------|--------|-----------|
| Backend | FastAPI + uvicorn | Async, WebSocket support, auto-docs |
| LLM Client | `openai` SDK | Works with OpenAI and any `/v1/chat/completions` endpoint |
| Frontend | Vanilla HTML/CSS/JS | Minimal dependencies, fast to build |
| Database | SQLite via `aiosqlite` | Same as opencode, async-compatible |
| Config | TOML (`tomllib`) | Python 3.11+ stdlib |
| Markdown | `markdown` + Pygments | Render assistant responses with syntax highlighting |
| HTTP Client | `httpx` | For webfetch tool, async |

---

## Project Structure

```
CodeAssist/
├── DESIGN.md
├── config.toml
├── requirements.txt
├── codeassist/               # Core package
│   ├── __main__.py           # CLI entry point
│   ├── server.py             # FastAPI app, routes, WebSocket
│   ├── agent.py              # Core agent loop (prompt → tool calls → execute → repeat)
│   ├── llm.py                # LLM client (OpenAI-compatible streaming)
│   ├── config.py             # Configuration loading
│   ├── prompts.py            # System prompt construction
│   ├── session.py            # Session/message persistence (SQLite)
│   ├── session_hook.py       # Session lifecycle hooks
│   ├── tokens.py             # Token counting and context window management
│   ├── knowledge.py          # Knowledge base CRUD and search
│   ├── embeddings.py         # Vector embeddings for semantic search
│   ├── trust_registry.py     # Tool trust/approval system
│   ├── lsp_client.py         # Language Server Protocol client
│   ├── mcp_client.py         # Model Context Protocol client
│   ├── plugins.py            # Plugin system
│   ├── agents.py             # Agent configuration and management
│   ├── session_manager.py    # Session fork/export/import
│   ├── dynamic_tools.py      # Dynamic tool loading
│   ├── custom_tools_loader.py# Custom tool discovery
│   ├── cli.py                # CLI interface
│   └── routes/               # API route modules
│       ├── config.py, sessions.py, skills.py, tools.py
│       ├── git.py, knowledge.py, mcp.py, plugins.py
│       ├── kb_gui.py, lsp.py, agents.py, custom_tools.py
├── tools/                    # Tool implementations
│   ├── __init__.py           # ToolRegistry, Tool base class, ToolResult
│   ├── read.py, write.py, edit.py, shell.py, glob.py, grep.py
│   ├── webfetch.py, todo.py, git.py, fossil.py, database.py
│   ├── directory.py, apply_patch.py, documentation.py, http.py
│   ├── process.py, advanced.py (web search)
│   ├── security.py           # SSRF protection, path validation
│   ├── tool_manager.py       # Dynamic tool management
│   ├── create_skill.py, create_tool.py
├── codeassist/static/             # Web UI
│   ├── index.html, style.css, app.js
├── tests/                    # 198 tests
│   ├── conftest.py, test_agent.py, test_config.py, test_llm.py
│   ├── test_session.py, test_session_manager.py, test_agents.py
│   ├── test_routes.py, test_skills.py, test_trust_registry.py
│   ├── test_dynamic_tools.py
│   └── test_tools/           # Tool-specific tests
│       ├── test_git.py, test_fossil.py, test_apply_patch.py
│       ├── test_database.py, test_directory.py, test_documentation.py
│       ├── test_tool_manager.py, test_http.py, test_process.py
├── Dockerfile
├── docker-compose.yml
├── config.toml               # Your config (gitignored)
├── config.example.toml       # Config template
└── config.docker.toml        # Config template for Docker
```

---

## Core Components

### 1. Configuration (`config.toml`)

```toml
[llm]
provider = "openai"                    # "openai" or "custom"
model = "gpt-4o"                       # Model identifier
api_key = "env:OPENAI_API_KEY"         # Value or "env:VAR_NAME"
base_url = ""                          # Empty = OpenAI default, or llama.cpp URL
vision = false                         # Enable image attachments in chat (multimodal models only)

[llm.parameters]
temperature = 0.0
max_tokens = 8192
context_window = 128000

[server]
host = "0.0.0.0"
port = 8090
workspace = "."                         # Root directory for file operations

[agent]
max_iterations = 100                    # Max tool-call rounds per prompt
name = "CodeAssist"
```

### 2. LLM Client (`llm.py`)

Wraps the `openai` SDK to work with any OpenAI-compatible endpoint.

```python
class LLMClient:
    def __init__(self, config):
        self.client = openai.AsyncOpenAI(
            api_key=config.api_key,
            base_url=config.base_url or None,  # None = OpenAI default
        )
        self.model = config.model

    async def stream(self, messages, tools) -> AsyncIterator[LLMEvent]:
        """Stream chat completion, yielding events."""
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            tools=tools,           # OpenAI function-calling format
            tool_choice="auto",
            stream=True,
            stream_options={"include_usage": True},
        )
        async for chunk in response:
            yield ...  # Parse into TextDelta, ToolCall, ToolResult, Finish events
```

**Key design decisions:**
- Uses OpenAI's native tool-calling format (supported by llama.cpp and most compatible servers)
- Streaming via SSE chunks
- Falls back gracefully if endpoint doesn't support tools (text-only mode)

### 3. Agent Loop (`agent.py`)

The heart of the system. Mirrors opencode's `runLoop()` logic.

```python
class Agent:
    def __init__(self, llm: LLMClient, tools: ToolRegistry, session: Session):
        self.llm = llm
        self.tools = tools
        self.session = session

    async def run(self, user_message: str) -> AsyncIterator[AgentEvent]:
        # Save user message
        await self.session.add_message("user", user_message)

        # Cache tool schemas (computed once)
        tool_schemas = self.tools.schemas()

        for iteration in range(self.config.max_iterations):
            # Build messages from history
            messages = self.build_messages()

            # Check context limits, compact if needed
            ctx = check_context_limit(messages, tool_schemas=tool_schemas)
            if ctx["needs_compaction"]:
                messages = compact_messages(messages)

            # Stream LLM response (with 120s timeout)
            tool_calls = []
            async for event in self.llm.stream(messages, tool_schemas):
                yield event
                if isinstance(event, ToolCallEvent):
                    tool_calls.append(event)

            if not tool_calls:
                break

            # Execute tools in parallel via asyncio.gather()
            confirmed = [tc for tc in tool_calls if not self.needs_confirmation(tc)]
            results = await asyncio.gather(*[
                self.tools.execute(tc.name, tc.arguments) for tc in confirmed
            ])
            for tc, result in zip(confirmed, results):
                await self.session.add_message("tool", result.output, tool_call_id=tc.id)
                yield ToolResultEvent(tc.id, result)

        # Save assistant message
        await self.session.add_message("assistant", accumulated_text)
```

**Flow (mirrors opencode's `prompt.ts`):**
1. Save user message to DB
2. Enter loop (max N iterations)
3. Build message history from DB
4. Check context limits, compact if needed (two-level: summarize tool outputs → drop old tool messages)
5. Build system prompt + tool schemas
6. Call LLM with streaming (120s timeout)
7. If no tool calls → break (done)
8. Execute confirmed tools in parallel via `asyncio.gather()`
9. Loop back to step 3

### 4. Tool System (`tools/`)

Each tool follows a consistent pattern, inspired by opencode's `Tool.define()`:

```python
@dataclass
class ToolResult:
    output: str
    error: bool = False

class Tool:
    name: str
    description: str
    parameters: dict          # JSON Schema

    async def execute(self, **kwargs) -> ToolResult:
        raise NotImplementedError
```

**Tool implementations:**

#### `read` - Read file contents
```python
class ReadTool(Tool):
    name = "read"
    description = "Read a file. Returns numbered lines."
    parameters = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "Absolute path"},
            "offset": {"type": "integer", "description": "Start line (0-indexed)"},
            "limit": {"type": "integer", "description": "Max lines to return"},
        },
        "required": ["file_path"]
    }
```

#### `write` - Write file contents
```python
class WriteTool(Tool):
    name = "write"
    parameters = {
        "file_path": {"type": "string"},
        "content": {"type": "string"},
    }
```

#### `edit` - Surgical string replacement
```python
class EditTool(Tool):
    name = "edit"
    parameters = {
        "file_path": {"type": "string"},
        "old_string": {"type": "string"},
        "new_string": {"type": "string"},
        "replaceAll": {"type": "boolean", "default": False},
    }
    # Validates old_string exists, fails if multiple matches
```

#### `shell` - Execute shell commands
```python
class ShellTool(Tool):
    name = "shell"
    parameters = {
        "command": {"type": "string"},
        "timeout": {"type": "integer", "default": 120},
        "workdir": {"type": "string"},
    }
    # Runs via subprocess, captures stdout/stderr, enforces timeout
```

#### `glob` - Find files by pattern
```python
class GlobTool(Tool):
    name = "glob"
    parameters = {
        "pattern": {"type": "string"},    # e.g. "*.py", "src/**/*.ts"
        "path": {"type": "string"},        # Optional directory
    }
    # Uses pathlib.rglob or wcmatch
```

#### `grep` - Search file contents
```python
class GrepTool(Tool):
    name = "grep"
    parameters = {
        "pattern": {"type": "string"},    # Regex pattern
        "path": {"type": "string"},        # Directory to search
        "include": {"type": "string"},     # File pattern filter
        "exclude": {"type": "string"},     # File pattern to exclude
        "context": {"type": "integer"},    # Context lines before/after match
    }
    # Uses ripgrep (rg) if available, falls back to Python re
```

#### `webfetch` - Fetch web content
```python
class WebFetchTool(Tool):
    name = "webfetch"
    parameters = {
        "url": {"type": "string"},
        "format": {"type": "string", "enum": ["text", "markdown"], "default": "markdown"},
    }
    # Uses httpx, converts HTML to markdown
```

### 5. System Prompt (`prompts.py`)

Constructed from components, following opencode's `system.ts` pattern:

```python
def build_system_prompt(config, working_dir, model_id) -> str:
    parts = []

    # Base agent prompt
    parts.append(BASE_PROMPT)

    # Environment block (mirrors opencode's sys.environment())
    parts.append(f"""<env>
  Working directory: {working_dir}
  Platform: {sys.platform}
  Today's date: {date.today().isoformat()}
</env>""")

    # Tool usage instructions
    parts.append(TOOL_INSTRUCTIONS)

    return "\n\n".join(parts)
```

The base prompt establishes the agent identity, coding conventions, and behavioral rules (matching opencode's approach of provider-specific prompts).

### 6. Session Persistence (`session.py`)

SQLite database, mirroring opencode's schema:

```sql
CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    name TEXT,
    created_at TIMESTAMP,
    updated_at TIMESTAMP
);

CREATE TABLE messages (
    id TEXT PRIMARY KEY,
    session_id TEXT REFERENCES sessions(id),
    role TEXT NOT NULL,         -- "user" | "assistant" | "tool" | "system"
    content TEXT,
    tool_call_id TEXT,          -- For tool result messages
    tool_calls TEXT,            -- JSON array of tool calls (for assistant messages)
    created_at TIMESTAMP
);

-- v6+: image attachments. `content` stays TEXT; attachments live here and are
-- joined back onto messages when loaded.
CREATE TABLE message_attachments (
    id TEXT PRIMARY KEY,
    message_id TEXT REFERENCES messages(id) ON DELETE CASCADE,
    attachment_type TEXT NOT NULL DEFAULT 'image',
    mime_type TEXT NOT NULL,
    file_name TEXT,
    data TEXT NOT NULL,        -- full "data:<mime>;base64,..." URL
    created_at TIMESTAMP
);
```

### 7. WebSocket Protocol

Real-time streaming from server to client:

```
Server → Client events:
  {"type": "text_delta",      "content": "Here is..."}
  {"type": "tool_call",       "id": "call_123", "name": "read", "arguments": {...}}
  {"type": "tool_result",     "id": "call_123", "output": "1: import os..."}
  {"type": "thinking",        "content": "I need to check..."}
  {"type": "error",           "message": "Rate limit exceeded"}
  {"type": "confirm_request", "id": "...", "tool": "write", "arguments": {...}}
  {"type": "context",         "tokens": 45000, "usage_pct": 35.2, "severity": "ok"}
  {"type": "compacted",       "message": "Context window compressed"}
  {"type": "finish",          "reason": "stop", "usage": {...}}
  {"type": "plan_update",     "tasks": [...]}
  {"type": "cancelled"}
  {"type": "done"}

Client → Server events:
  {"type": "user_message",    "content": "What is this?", "images": ["data:image/png;base64,..."]}
  {"type": "cancel"}          # Abort current run
  {"type": "confirm_response", "id": "...", "approved": true}
  {"type": "switch_agent",    "agent": "plan"}
```

`images` is an optional array of base64 `data:` URLs. When present, the user
message content is converted to OpenAI multipart format
(`[{"type":"text",...}, {"type":"image_url",...}]`) before being sent to the LLM.
Limits: 4 images/message, 8 MB each, MIME PNG/JPEG/WebP/GIF. Token counting
charges a flat ~1000 tokens per image and media parts are stripped during
context compaction (text summary keeps a `[N image attachment(s)]` marker).

### 8. Frontend (`codeassist/static/`)

A single-page chat interface:

- **Layout:** Sidebar (sessions) + main chat area + optional tool output panel
- **Streaming:** WebSocket connection, append text deltas in real-time
- **Markdown:** Render assistant messages with vendored `marked.js` + `highlight.js` (bundled in `codeassist/static/vendor/`, no CDN dependency)
- **Tool display:** Collapsible sections showing tool calls and their output
- **Session management:** Create/switch/delete sessions
- **Model selector:** Switch between configured models
- **Dark theme:** Clean, developer-focused UI

---

## Key Differences from OpenCode

| Aspect | OpenCode | CodeAssist |
|--------|----------|------------|
| Language | TypeScript/Bun | Python 3.11+ |
| Framework | Effect v4 | Plain async/await |
| LLM providers | 15+ via AI SDK | 1 via OpenAI-compatible API |
| Protocols | MCP, LSP, ACP | MCP client, LSP client (full implementation) |
| UI | TUI + Web + Desktop | Web only |
| Database | SQLite + Drizzle | SQLite + aiosqlite |
| Tools | 41+ with permissions | 27 tools with trust/approval |
| Agent types | build, plan, general, custom | 3 built-in (default, research, review) + dynamic |
| Git integration | Snapshots, diffs, reverts | Shell-based + git_snapshot tool |
| Config | JSONC with schema | TOML |
| Streaming | SSE + WebSocket | WebSocket only |
| Context window | Compaction | Two-level compaction (summarize → drop) |
| Parallel tools | Sequential | Parallel via asyncio.gather() |
| SSRF protection | N/A (local only) | Full DNS validation, internal TLD blocking |
| Custom tools | N/A | User-written Python files auto-discovered |
| Skills | N/A | Markdown files with instructions (4 built-in) |
| Plugins | N/A | Python modules with hooks |
| Cost tracking | N/A | Real-time token budget enforcement |
| Workers | N/A | Local workstation daemon (planned) |

---

## Implementation Phases

### Phase 1: Core Agent (MVP) ✅ Complete
- [x] Config loading (TOML)
- [x] LLM client with OpenAI-compatible streaming
- [x] Agent loop with tool calling
- [x] Tool registry + base class
- [x] 5 core tools: read, write, edit, shell, glob
- [x] Session persistence (SQLite)
- [x] System prompt construction

### Phase 2: Web UI ✅ Complete
- [x] FastAPI server with WebSocket
- [x] Chat UI (HTML/CSS/JS)
- [x] Streaming text display
- [x] Tool call/result visualization
- [x] Session list/management
- [x] Markdown rendering

### Phase 3: Polish ✅ Complete
- [x] Grep tool (ripgrep or Python fallback)
- [x] Webfetch tool
- [x] Todo tool
- [x] Error handling and retries
- [x] Configuration validation
- [x] Model switching
- [x] SSRF protection, DNS rebinding prevention
- [x] ToolResult return type for all tools
- [x] Context window management (two-level compaction)
- [x] Parallel tool execution
- [x] Streaming timeout (120s)
- [x] Write tool backup, grep exclude/context, edit stale-edit detection

### Phase 4: Advanced Features
- [x] Multi-agent support (default, research, review)
- [x] MCP client integration
- [x] LSP client (full implementation with response parsing)
- [x] Plugin support
- [x] Custom tools (user-written Python files)
- [x] Skills system (4 built-in: refactor, debug, optimize, migrate)
- [x] Trust registry (tool approval)
- [x] Session manager (fork/export/import)
- [x] Git snapshot/revert tool
- [x] Diff preview tool
- [x] Test runner with framework auto-detection
- [x] Symbol search (ctags-based)
- [x] Package manager detection and management
- [x] Docker container management
- [x] Image analysis (vision-capable LLMs)
- [x] Cost tracker (real-time budget enforcement)
- [x] Question tool (agent can ask user mid-task)

### Phase 5: Local Workstation Daemon (planned)
- [ ] Worker registry on server
- [ ] `worker.py` daemon with tool execution
- [ ] Tool routing (worker vs local)
- [ ] Session binding UI
- [ ] Authentication (API key or token)

---

## Estimated Effort

| Phase | Time | Deliverable |
|-------|------|-------------|
| Phase 1 | ✅ Done | Working agent with tools |
| Phase 2 | ✅ Done | Web UI with streaming |
| Phase 3 | ✅ Done | Polished, production-ready |
| Phase 4 | ✅ Done | Multi-agent, MCP, LSP, plugins, custom tools |
| Phase 5 | ~1 day | Local workstation daemon |

---

## Dependencies (`requirements.txt`)

```
fastapi>=0.110.0
uvicorn[standard]>=0.29.0
websockets>=12.0
openai>=1.30.0
aiosqlite>=0.20.0
httpx>=0.27.0
markdown>=3.6
pygments>=2.18.0
tOML>=0.10.2    # Only needed for Python < 3.11
```

All dependencies are well-established, actively maintained, and have minimal transitive dependencies.

---

## Local Workstation Daemon (Phase 5)

### Problem

Currently all tools (read, write, edit, shell, glob, grep) run on the server's filesystem. Developers want tools to execute on their local workstation instead.

### Architecture

```
┌─────────────────────────────────────────────────────┐
│              Web App (server.py)                     │
│                                                     │
│  Tool calls → Worker Registry → Route to worker     │
│                              ← Return results       │
└────────────────────┬────────────────────────────────┘
                     │ WebSocket
┌────────────────────▼────────────────────────────────┐
│          Local Daemon (worker.py)                    │
│          Runs on developer's workstation             │
│                                                     │
│  Connects to server as a "worker"                   │
│  Receives tool execution requests                   │
│  Executes tools against LOCAL filesystem            │
│  Returns results to server                          │
│                                                     │
│  Usage:                                              │
│    python worker.py --server ws://your-server:8090   │
│    python worker.py --server ws://your-server:8090 --workspace ~/myproject
└─────────────────────────────────────────────────────┘
```

### Protocol

The daemon connects to a dedicated WebSocket endpoint:

```
Server: /ws/worker/{worker_id}
Daemon:  ws://server:8090/ws/worker
```

**Messages (server → daemon):**
```json
{"type": "execute", "request_id": "abc123", "tool": "read", "args": {"file_path": "/local/file.py"}}
```

**Messages (daemon → server):**
```json
{"type": "result", "request_id": "abc123", "output": "1: import os\n2: ..."}
{"type": "registered", "worker_id": "...", "workspace": "/home/dev/myproject"}
```

### Server Changes

1. **Worker Registry** -- tracks connected workers
2. **Tool Routing** -- when a worker is connected, route tool calls to it instead of executing locally
3. **Fallback** -- if no worker is connected, execute tools on the server (current behavior)
4. **Session binding** -- a session can optionally be "bound" to a specific worker

### Daemon Implementation (`worker.py`)

```python
# worker.py - Local workstation daemon
# Connects to CodeAssist server, executes tools on local filesystem

class Worker:
    def __init__(self, server_url: str, workspace: Path):
        self.server_url = server_url
        self.workspace = workspace
        self.tools = create_registry(workspace)

    async def connect(self):
        async with websockets.connect(self.server_url) as ws:
            # Register with server
            await ws.send(json.dumps({
                "type": "register",
                "workspace": str(self.workspace),
            }))

            # Listen for tool execution requests
            async for msg in ws:
                data = json.loads(msg)
                if data["type"] == "execute":
                    result = await self.tools.execute(data["tool"], data["args"])
                    await ws.send(json.dumps({
                        "type": "result",
                        "request_id": data["request_id"],
                        "output": result,
                    }))

# Usage:
#   python worker.py --server ws://your-server:8090
#   python worker.py --server ws://your-server:8090 --workspace ~/myproject
```

### Config Changes

```toml
[worker]
# Enable worker mode (daemon connects to remote server)
enabled = false
server = "ws://your-server:8090"
workspace = "."  # Local directory to expose to the server
```

### Implementation Priority

| Step | Description | Effort |
|------|-------------|--------|
| 1 | Worker registry on server | 1 hour |
| 2 | `worker.py` daemon with tool execution | 2 hours |
| 3 | Tool routing (worker vs local) | 1 hour |
| 4 | Session binding UI | 2 hours |
| 5 | Authentication (API key or token) | 1 hour |
| **Total** | | **~1 day** |
