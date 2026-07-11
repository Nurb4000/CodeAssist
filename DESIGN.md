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
├── server.py              # FastAPI app, routes, WebSocket
├── agent.py               # Core agent loop (prompt → tool calls → execute → repeat)
├── llm.py                 # LLM client (OpenAI-compatible streaming)
├── tools/
│   ├── __init__.py        # Tool registry + base Tool class
│   ├── read.py            # Read file contents
│   ├── write.py           # Write file contents
│   ├── edit.py            # Surgical string replacement
│   ├── shell.py           # Execute shell commands
│   ├── glob.py            # Find files by pattern
│   ├── grep.py            # Search file contents
│   ├── webfetch.py        # Fetch and parse web content
│   └── todo.py            # Manage task lists
├── session.py             # Session/message persistence (SQLite)
├── prompts.py             # System prompt construction
├── static/
│   ├── index.html         # Chat UI
│   ├── style.css
│   └── app.js             # Frontend logic (WebSocket, streaming, markdown)
└── tests/
    ├── test_agent.py
    ├── test_tools.py
    └── test_llm.py
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

[llm.parameters]
temperature = 0.0
max_tokens = 8192
context_window = 128000

[server]
host = "0.0.0.0"
port = 8000
workspace = "."                         # Root directory for file operations

[agent]
max_iterations = 30                     # Max tool-call rounds per prompt
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

        for iteration in range(self.config.max_iterations):
            # Build messages from history
            messages = self.build_messages()

            # Build tool schemas for LLM
            tool_schemas = self.tools.schemas()

            # Stream LLM response
            tool_calls = []
            async for event in self.llm.stream(messages, tool_schemas):
                yield event
                if isinstance(event, ToolCallEvent):
                    tool_calls.append(event)

            if not tool_calls:
                # No tool calls = LLM is done
                break

            # Execute tools and append results
            for tc in tool_calls:
                result = await self.tools.execute(tc.name, tc.arguments)
                await self.session.add_message("tool", result, tool_call_id=tc.id)
                yield ToolResultEvent(tc.id, result)

        # Save assistant message
        await self.session.add_message("assistant", accumulated_text)
```

**Flow (mirrors opencode's `prompt.ts`):**
1. Save user message to DB
2. Enter loop (max N iterations)
3. Build message history from DB
4. Build system prompt + tool schemas
5. Call LLM with streaming
6. If no tool calls → break (done)
7. Execute each tool call, save results
8. Loop back to step 3

### 4. Tool System (`tools/`)

Each tool follows a consistent pattern, inspired by opencode's `Tool.define()`:

```python
class Tool:
    name: str
    description: str
    parameters: dict          # JSON Schema

    async def execute(self, **kwargs) -> str:
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
  {"type": "done"}

Client → Server events:
  {"type": "user_message",    "content": "Fix the bug in main.py"}
  {"type": "cancel"}          # Abort current run
```

### 8. Frontend (`static/`)

A single-page chat interface:

- **Layout:** Sidebar (sessions) + main chat area + optional tool output panel
- **Streaming:** WebSocket connection, append text deltas in real-time
- **Markdown:** Render assistant messages with `marked.js` + syntax highlighting via `highlight.js`
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
| Protocols | MCP, LSP, ACP | None (future) |
| UI | TUI + Web + Desktop | Web only |
| Database | SQLite + Drizzle | SQLite + aiosqlite |
| Tools | 41+ with permissions | 8 core tools, no permissions |
| Agent types | build, plan, general, custom | Single agent (extensible) |
| Git integration | Snapshots, diffs, reverts | None (future) |
| Config | JSONC with schema | TOML |
| Streaming | SSE + WebSocket | WebSocket only |

---

## Implementation Phases

### Phase 1: Core Agent (MVP)
- [ ] Config loading (TOML)
- [ ] LLM client with OpenAI-compatible streaming
- [ ] Agent loop with tool calling
- [ ] Tool registry + base class
- [ ] 5 core tools: read, write, edit, shell, glob
- [ ] Session persistence (SQLite)
- [ ] System prompt construction

### Phase 2: Web UI
- [ ] FastAPI server with WebSocket
- [ ] Chat UI (HTML/CSS/JS)
- [ ] Streaming text display
- [ ] Tool call/result visualization
- [ ] Session list/management
- [ ] Markdown rendering

### Phase 3: Polish
- [ ] Grep tool (ripgrep or Python fallback)
- [ ] Webfetch tool
- [ ] Todo tool
- [ ] Error handling and retries
- [ ] Configuration validation
- [ ] Model switching

### Phase 4: Advanced Features (optional)
- [ ] Multi-agent support (plan mode)
- [ ] MCP client integration
- [ ] Git snapshot/revert
- [ ] LSP diagnostics
- [ ] Permission system
- [ ] Plugin support

---

## Estimated Effort

| Phase | Time | Deliverable |
|-------|------|-------------|
| Phase 1 | 2-3 days | Working CLI-level agent with tools |
| Phase 2 | 2-3 days | Web UI with streaming |
| Phase 3 | 2-3 days | Polished, production-ready |
| Phase 4 | Ongoing | Feature additions |

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
