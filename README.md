# CodeAssist

AI coding assistant that runs **locally on your machine**. Connects to OpenAI or any OpenAI-compatible endpoint (llama.cpp, vLLM, etc.) and gives you a web-based chat UI with file editing, shell execution, code search, and more.

## Why local?

CodeAssist is designed to run on your development machine, not a remote server. This means:

- **Tools access your local filesystem directly** -- no file sync, no daemons
- **Shell commands run on your machine** -- your env, your tools, your dependencies
- **No network latency** -- everything runs on localhost
- **Your code never leaves your machine** (except to the LLM API you configure)

## Quick Start

> **Always specify your project folder.** CodeAssist operates on whatever directory you give it. Running it without `--workspace` defaults to the current directory, which may not be what you intended. Get in the habit of always pointing it at the project you want to work on.

### With conda (recommended)

```bash
# Create environment
conda create -n codeassist python=3.12
conda activate codeassist

# Install
cd CodeAssist
pip install -e .

# Configure
cp config.example.toml config.toml
# Edit config.toml -- add your API key or llama.cpp server URL

# Run -- always specify your project folder
codeassist --workspace ~/Projects/myapp
```

### With venv

```bash
python -m venv .venv
source .venv/bin/activate

pip install -e .

cp config.example.toml config.toml
# Edit config.toml

# Always specify your project folder
codeassist --workspace ~/Projects/myapp
```

### Without installing

```bash
python -m codeassist --workspace ~/Projects/myapp
```

### With Docker

Isolates the runtime in a container while mounting your project directory for full access.

```bash
# Copy the Docker example config
cp config.docker.toml config.toml
# Edit config.toml -- add your API key

# Build and start -- always point WORKSPACE at your project
WORKSPACE=~/Projects/myapp docker compose up --build
```

The server starts at `http://localhost:8090`. Session history persists across restarts via a Docker volume.

**Port configuration:** The container reads the port from `config.toml` (default: 8090). This avoids conflicts with common tools like Portainer (which uses 8000).

To use a different port:
```bash
# Option 1: Edit port in config.toml
# Option 2: Use environment variables
HOST_PORT=9000 SERVER_PORT=9000 docker compose up --build
```

Environment variable overrides:
- `CODEASSIST_WORKSPACE` -- override the workspace path inside the container
- `HOST_PORT` -- host port to expose (default: 8090)
- `SERVER_PORT` -- container port (must match config.toml)

## Important: How CodeAssist Works

CodeAssist is an **agentic** tool. Once you give it a prompt, it can:

- Read and write files anywhere in the workspace directory
- Execute shell commands (build scripts, git, test runners, etc.)
- Search across your entire codebase

It will ask for confirmation before making changes or running commands, but **you are granting it full access to the directory you specify**. This is by design -- it needs that access to be useful -- but it also means:

- **Always use `--workspace`** to scope it to the project you are working on
- **Never point it at your home directory** or any directory more broad than necessary
- **Review the confirmation dialogs** before approving operations, especially shell commands
- **Use Docker** if you want an additional isolation layer between the agent and your system

## Configuration

Edit `config.toml`:

```toml
[llm]
# For OpenAI:
model = "gpt-4o"
api_key = "sk-your-key-here"
base_url = ""

# Enable image attachments in chat (attach / paste / drop):
# Only set to true if your model supports image input
# (e.g. gpt-4o, gpt-4o-mini, or a multimodal llama.cpp model).
vision = true

# For llama.cpp:
# model = "your-model-name"
# api_key = "none"
# base_url = "http://localhost:8080/v1"
```

See `config.example.toml` for all options (agent settings, tool limits, MCP, skills, LSP, and more).

### Image attachments

When `vision = true` and the model supports images, the chat UI shows an attach button. You can:

- Click the paperclip to pick images (PNG, JPEG, WebP, GIF)
- Paste an image directly into the input box (`Ctrl`/`Cmd`+`V`)
- Drag & drop image files onto the input area

Limits: up to **4 images per message**, **8 MB each**. Attached images are sent to the model as OpenAI-style `image_url` parts and are persisted in the session history (including across forks/session exports). If the model is not multimodal, leave `vision = false` to hide the attach button.

### Securing the server

If you expose the server beyond localhost (e.g., `host = "0.0.0.0"`), set a password in `config.toml`:

```toml
[server]
password = "your-secret-here"
```

Without a password, anyone who can reach the port has full access to the workspace.

### Security features

CodeAssist includes several security layers:

- **SSRF protection** (`tools/security.py`): DNS rebinding prevention, internal TLD blocking (`.internal`, `.local`, `.corp`), cloud metadata IP blocking (`169.254.169.254`)
- **Workspace path validation**: All file operations are validated to stay within the workspace directory
- **Tool trust system**: Custom tools are scanned for dangerous patterns and require explicit approval
- **Auth middleware**: Password-based HTTP Basic Auth with WebSocket support via `sec-websocket-protocol` header
- **Secure WebSocket**: Frontend derives `wss://` or `ws://` from page protocol automatically
- **Vendored frontend dependencies**: `highlight.js` and `marked` are bundled locally in `codeassist/static/vendor/` instead of loaded from CDNs, eliminating external network requests at page load and removing the attack surface from CDN compromise or supply-chain attacks

### Context window management

CodeAssist automatically manages context window limits during long sessions:

- **Token counting**: Estimates token usage including tool schema overhead for accurate thresholds
- **Two-level compaction**: When context exceeds 75%, old messages are compacted:
  - **Level 0**: Tool outputs summarized to 1-line, assistant content truncated, user messages preserved
  - **Level 1** (escalation): Old tool messages dropped entirely, only function names kept
- **Compaction caching**: Compacted messages are cached and only recomputed when new messages arrive
- **Smart truncation**: Tool output truncation preserves error/traceback lines for debugging

Configuration in `config.toml`:
```toml
[compaction]
enabled = true
threshold_pct = 75      # Trigger compaction at this usage percentage
keep_recent = 20        # Number of recent messages to preserve unchanged
tool_result_max_tokens = 4000  # Max tokens per tool output
```

### Tuning & reliability

CodeAssist includes several quality-of-life improvements:

- **Message cache** — session history is fetched from the database once per iteration, then tracked in-memory with a dirty flag. Subsequent iterations reuse the cache unless new messages were added.
- **Streaming persistence** — assistant messages are saved to the database at stream start (as a placeholder) and updated when the stream completes. If the server crashes mid-stream, the partial message is preserved.
- **Embedding throttling** — concurrent embedding generation is capped at 2 tasks via `asyncio.Semaphore`, preventing overload of the embedding API.
- **Session hook lock** — `on_session_end` processing is serialized per-singleton with `asyncio.Lock`, preventing races between concurrent WebSocket disconnects.
- **Configurable web search engine** — choose the backend via `config.toml`:
  ```toml
  [tools]
  websearch_engine = "duckduckgo"  # or "generic" (HTML scrape fallback)
  ```

### Agent behavior

- **Parallel tool execution**: Multiple independent tool calls from the LLM execute simultaneously via `asyncio.gather()`
- **Streaming timeout**: LLM streams time out after 120 seconds with a graceful error
- **Max-iteration limit**: When the agent reaches the maximum iteration count, it notifies the user rather than silently stopping
- **Confirmation prompts**: Destructive operations (file writes, shell commands) require user confirmation unless workspace is trusted
- **Cost tracking**: Real-time token usage tracking with configurable budget limits (tokens and cost per session)
- **Question flow**: The agent can pause and ask the user questions mid-task via the `question` tool

## Usage

```bash
# Always specify your project
codeassist --workspace ~/Projects/myapp

# Custom port
codeassist --workspace ~/Projects/myapp --port 9000

# Don't auto-open browser
codeassist --workspace ~/Projects/myapp --no-browser
```

For a screen-by-screen walkthrough of the chat, Admin, Knowledge Base, and Tool Manager interfaces,
see [`docs/user-guide.md`](docs/user-guide.md).

## What it does

- **Chat with an AI** that can read, write, and edit your code
- **Run shell commands** through the chat (build, test, git, etc.)
- **Search code** with regex patterns
- **Find files** with glob patterns
- **Fetch web content** for documentation lookup
- **Track tasks** across multi-step work
- **Continue** conversations with context
- **Knowledge base** - persistent learning across sessions with semantic search

## Knowledge Base

CodeAssist learns from every session and builds a persistent knowledge base:

- **Session Summaries** - AI-generated summaries with key topics and quality scores
- **Knowledge Extraction** - Automatically captures patterns, conventions, and decisions
- **Full-Text Search** - FTS5 search across all knowledge (instant)
- **Semantic Search** - Vector embeddings for similarity search (requires embedding model)
- **Tool Analytics** - Track tool usage, success rates, and performance
- **LLM Cost Tracking** - Monitor token usage and estimated costs
- **File History** - Track modifications across sessions
- **Fine-Tuning Ready** - Structured Q&A pairs for future model training

All data stored in human-readable SQLite - query directly with SQL, DB Browser, or Python.

```bash
# Enable semantic search (optional)
# Add to config.toml:
[llm]
embedding_model = "text-embedding-3-small"
```

See `docs/knowledge-base-quickref.md` for API endpoints and examples.

## Knowledge Base GUI

Access the KB dashboard via the **📚 icon** in the sidebar or the **Knowledge Base** link in the footer.

### Features

| Page | Purpose |
|------|---------|
| **Dashboard** | Overview stats, entry counts, recent activity |
| **Entries** | Browse, filter, edit, delete knowledge entries |
| **Search** | Full-text and semantic search across all knowledge |
| **Sessions** | View session history with summaries |
| **Analytics** | Tool usage charts, LLM cost tracking |
| **PII Manager** | Scan for and redact personal information |
| **Settings** | Configure auto-creation, confidence thresholds |
| **Export/Import** | Download/upload KB data, clear entire KB |

### PII Protection

The PII Manager automatically scans for:
- Email addresses
- IP addresses
- Phone numbers
- SSNs
- Credit card numbers
- API keys

Review flagged entries and redact or delete as needed.

## Self-Creation System

CodeAssist can automatically create skills and tools when it detects repetitive patterns in your workflow.

### How It Works

1. **Pattern Detection** - Monitors tool call sequences across sessions
2. **Repetition Recognition** - Identifies workflows repeated 3+ times
3. **Auto-Creation** - Creates skills when confidence threshold is met
4. **Hot-Reload** - New skills available immediately (no restart)

### Configuration

```toml
[agent]
auto_create_skills = true      # Auto-create skills for repetitive workflows
auto_create_tools = false      # Disabled by default (security)
max_auto_creations = 3         # Per session limit
min_confidence = 0.7           # Threshold for auto-creation
```

### Custom Tools

You can create custom Python tools in `.codeassist/custom_tools/`:

```python
# .codeassist/custom_tools/my_tool.py
from tools import ToolResult

TOOLS = {
    "my_tool": {
        "name": "my_tool",
        "description": "Does something useful",
        "parameters": {
            "type": "object",
            "properties": {
                "input": {"type": "string"}
            }
        }
    }
}

async def execute(input: str) -> ToolResult:
    return ToolResult(output=f"Processed: {input}")
```

### Management

- **API**: `GET /api/auto-creation/status` - View auto-creation stats
- **API**: `POST /api/skills/reload` - Reload skills from disk
- **API**: `POST /api/custom-tools/reload` - Reload custom tools
- **GUI**: `/static/kb.html` - Knowledge Base dashboard

See `docs/knowledge-base-quickref.md` for full API reference.

### Built-in tools

| Tool | Description |
|------|-------------|
| `read` | Read file contents with line numbers, offset/limit support |
| `write` | Write or overwrite files, creates parent directories, backs up existing files to `.bak` |
| `edit` | Surgical string replacement with stale-edit detection and similar-content hints |
| `shell` | Execute shell commands with timeout |
| `glob` | Find files matching glob patterns |
| `grep` | Search file contents with regex, supports `exclude` patterns and `context` lines (uses ripgrep if available) |
| `webfetch` | Fetch and return content from a URL |
| `websearch` | Search the web for information |
| `todo` | Manage a task list across multi-step work |
| `git` | Full git operations: status, diff, log, commit, push, pull, branch, clone, worktree, apply patch |
| `fossil` | Fossil VCS operations: status, diff, log, commit, checkout, branch, tag |
| `database` | Execute SQL queries against SQLite databases |
| `directory` | List directory contents with metadata |
| `apply_patch` | Apply unified diff patches atomically across multiple files |
| `documentation` | Generate documentation from source code (Python, JS, TS) |
| `http` | Make HTTP requests to REST APIs |
| `process` | Manage long-running background processes |
| `question` | Ask the user a question and wait for their response |
| `create_skill` | Create new skills for repetitive workflows |
| `create_tool` | Create custom Python tools |
| `diff_preview` | Show unified diff before applying edits |
| `test_runner` | Auto-detect test framework and run tests with structured results |
| `symbol_search` | Go-to-definition and find-references using ctags |
| `package_manager` | Detect and manage dependencies (pip, npm, yarn, cargo, etc.) |
| `git_snapshot` | Auto-commit workspace state for safe experimentation |
| `docker` | Container management (build, run, stop, logs, compose) |
| `image_analyze` | Analyze screenshots and mockups using vision-capable LLMs |
| `lsp` | Query language servers for diagnostics, completions, and formatting |

### Agent Types

CodeAssist supports multiple agent types with different tool permissions:

| Agent | Purpose | Tools Allowed |
|-------|---------|---------------|
| **CodeAssist** (default) | Full development agent | All tools (writes require confirmation) |
| **Research** | Read-only research | read, grep, glob, websearch, webfetch, symbol_search |
| **Review** | Code review | read, grep, glob, test_runner, diff_preview, symbol_search |

### Tool Manager

Access the Tool Manager via the **🔧 icon** in the sidebar or the **Tool Manager** link in the footer.

| Page | Purpose |
|------|---------|
| **All Tools** | Browse built-in and custom tools |
| **Custom Tools** | Review, trust/untrust, delete custom tools |
| **Security Scan** | Scan for dangerous code patterns |
| **Usage Stats** | View tool usage statistics |

**Security:** Custom tools are scanned for potentially dangerous patterns (network access, file operations, subprocess calls, etc.). Review and trust tools before allowing unrestricted execution.

### Skills

Skills are reusable, guided workflows that extend CodeAssist's capabilities. They're markdown files with frontmatter that define specialized instructions for specific tasks.

**Using skills:** Type the slash command (e.g., `/review`, `/music`) in chat to invoke a skill. You can also mention the skill by name naturally (e.g., "review this code" or "help me debug this").

**Built-in coding skills:**

| Skill | Slash | Purpose |
|-------|-------|---------|
| `code-review` | `/review` | Review code for bugs, security, and quality |
| `refactor` | `/refactor` | Systematic refactoring with safety checks and test verification |
| `debug` | `/debug` | Step-by-step debugging workflow |
| `test` | `/test` | Write unit and integration tests |
| `explain` | `/explain` | Explain how code works |
| `document` | `/doc` | Generate docstrings and documentation |
| `optimize` | `/optimize` | Data-driven performance profiling and optimization |
| `clean` | `/clean` | Remove dead code, organize imports |
| `security` | `/security` | Audit for vulnerabilities |
| `convert` | `/convert` | Convert between languages/frameworks |
| `generate` | `/generate` | Generate boilerplate code |
| `migrate` | `/migrate` | Database migration and data transformation |
| `lint` | `/lint` | Fix linting and formatting issues |

**Non-coding skill examples:**

| Skill | Slash | Purpose |
|-------|-------|---------|
| `music` | `/music` | Generate structured song parameters for ACE-Step music generation |
| `imagegen` | `/imagegen` | Generate Stable Diffusion prompts with proper syntax, weights, and negatives |

These skills demonstrate how CodeAssist's skill system extends beyond coding tasks:

- **`music`** generates properly formatted JSON payloads (caption, lyrics, metadata) for music generation engines, enforcing structure rules and duration-to-lyric mapping.
- **`imagegen`** produces complete Stable Diffusion prompts with correct token weighting syntax `(word:1.3)`, positive/negative prompt separation, and style-specific templates.

Both show the platform's flexibility for any domain where consistent, structured LLM output is valuable — creative tools, content generation, data formatting, and more.

**Creating custom skills:**

Add markdown files to `.codeassist/skills/` with this structure:

```markdown
---
name: my-skill
description: What this skill does and when to use it
slash: command
---

# Skill Name

Instructions and rules here...
```

Skills are discovered automatically on startup.

## Project structure

```
CodeAssist/
├── __main__.py              # CLI entry point
├── codeassist/              # Core package
│   ├── server.py            # FastAPI web server
│   ├── agent.py             # Agent loop (prompt -> tool calls -> repeat)
│   ├── llm.py               # OpenAI-compatible streaming client
│   ├── config.py            # Configuration loading
│   ├── prompts.py           # System prompt construction
│   ├── session.py           # SQLite session persistence
│   ├── session_hook.py      # Session lifecycle hooks (summarization, knowledge extraction)
│   ├── session_manager.py   # Session fork/export/import
│   ├── tokens.py            # Token counting and context window management
│   ├── knowledge.py         # Knowledge base CRUD and search
│   ├── embeddings.py        # Vector embeddings for semantic search
│   ├── agents.py            # Agent configuration and management (default, research, review)
│   ├── cost_tracker.py      # Real-time token budget enforcement
│   ├── trust_registry.py    # Tool trust/approval system
│   ├── lsp_client.py        # Language Server Protocol client (full implementation)
│   ├── mcp_client.py        # Model Context Protocol client
│   ├── plugins.py           # Plugin system
│   ├── dynamic_tools.py     # Dynamic tool loading
│   ├── custom_tools_loader.py # Custom tool discovery
│   ├── cli.py               # CLI interface
│   └── routes/              # API route modules
│       ├── config.py        # Configuration endpoints
│       ├── sessions.py      # Session management endpoints
│       ├── skills.py        # Skill management endpoints
│       ├── tools.py         # Tool management endpoints
│       ├── git.py           # Git endpoints
│       ├── knowledge.py     # Knowledge base endpoints
│       ├── mcp.py           # MCP endpoints
│       ├── plugins.py       # Plugin endpoints
│       ├── kb_gui.py        # Knowledge base GUI
│       ├── lsp.py           # LSP endpoints
│       ├── agents.py        # Agent management endpoints
│       └── custom_tools.py  # Custom tool endpoints
├── tools/                   # Tool implementations
│   ├── __init__.py          # ToolRegistry, Tool base class, ToolResult
│   ├── read.py              # Read file contents
│   ├── write.py             # Write file contents (with .bak backup)
│   ├── edit.py              # String replacement (with stale-edit detection)
│   ├── shell.py             # Execute shell commands
│   ├── glob.py              # Find files by pattern
│   ├── grep.py              # Search file contents (exclude, context lines)
│   ├── webfetch.py          # Fetch web content
│   ├── todo.py              # Task list management
│   ├── git.py               # Git operations
│   ├── fossil.py            # Fossil VCS operations
│   ├── database.py          # SQLite queries
│   ├── directory.py         # Directory listing
│   ├── apply_patch.py       # Unified diff patches
│   ├── documentation.py     # Source code documentation
│   ├── http.py              # HTTP requests
│   ├── process.py           # Background process management
│   ├── advanced.py          # Web search, question asking
│   ├── security.py          # SSRF protection, path validation, workspace enforcement
│   ├── tool_manager.py      # Dynamic tool management
│   ├── create_skill.py      # Skill creation
│   ├── create_tool.py       # Tool creation
│   ├── diff_preview.py      # Unified diff preview
│   ├── test_runner.py       # Test framework auto-detection and execution
│   ├── symbol_search.py     # ctags-based symbol search
│   ├── package_manager.py   # Dependency management
│   ├── git_snapshot.py      # Auto-commit for safe experimentation
│   ├── docker_tool.py       # Container management
│   └── image_analyze.py     # Vision-capable image analysis
├── .codeassist/             # Skills and plugins
│   └── skills/              # Skill markdown files
├── codeassist/static/        # Web UI
├── tests/                   # Test suite (198 tests)
├── Dockerfile               # Container image definition
├── docker-compose.yml       # One-command Docker startup
├── config.toml              # Your config (gitignored)
├── config.example.toml      # Config template
└── config.docker.toml       # Config template for Docker
```

## Requirements

- Python 3.11+
- An OpenAI-compatible API (OpenAI, llama.cpp, vLLM, etc.)
- Git (recommended, for repository operations)

A couple of screenshots of it in action.

<img width="1488" height="822" alt="image" src="https://github.com/user-attachments/assets/5ccccd2b-9d1e-44f0-a00b-20bef0020e8b" />
<img width="844" height="624" alt="image" src="https://github.com/user-attachments/assets/699085b7-b580-4fbc-b34a-c157a8134ab7" />
<img width="831" height="411" alt="image" src="https://github.com/user-attachments/assets/cb468233-6407-4c73-b775-46a32fc54d02" />
<img width="786" height="394" alt="image" src="https://github.com/user-attachments/assets/c778bf52-9c31-4982-8cb5-84ebbb844c2a" />
<img width="1761" height="665" alt="image" src="https://github.com/user-attachments/assets/65a4764d-d2a5-4df4-9201-5679a438f2c9" />
<img width="1878" height="570" alt="image" src="https://github.com/user-attachments/assets/a3167ec6-94c6-4167-be75-c8e0f108b1a2" />





## License

MIT
