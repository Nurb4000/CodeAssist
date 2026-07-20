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

The server starts at `http://localhost:8000`. Session history persists across restarts via a Docker volume.

Environment variable overrides (for advanced use):
- `CODEASSIST_WORKSPACE` -- override the workspace path inside the container
- `CODEASSIST_HOST` -- override the bind address
- `CODEASSIST_PORT` -- override the port

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

# For llama.cpp:
# model = "your-model-name"
# api_key = "none"
# base_url = "http://localhost:8080/v1"
```

See `config.example.toml` for all options (agent settings, tool limits, MCP, skills, LSP, and more).

### Securing the server

If you expose the server beyond localhost (e.g., `host = "0.0.0.0"`), set a password in `config.toml`:

```toml
[server]
password = "your-secret-here"
```

Without a password, anyone who can reach the port has full access to the workspace.

## Usage

```bash
# Always specify your project
codeassist --workspace ~/Projects/myapp

# Custom port
codeassist --workspace ~/Projects/myapp --port 9000

# Don't auto-open browser
codeassist --workspace ~/Projects/myapp --no-browser
```

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

async def execute(input: str) -> str:
    return f"Processed: {input}"
```

### Management

- **API**: `GET /api/auto-creation/status` - View auto-creation stats
- **API**: `POST /api/skills/reload` - Reload skills from disk
- **API**: `POST /api/custom-tools/reload` - Reload custom tools

See `docs/knowledge-base-quickref.md` for full API reference.

### Built-in tools

| Tool | Description |
|------|-------------|
| `read` | Read file contents with line numbers, offset/limit support |
| `write` | Write or overwrite files, creates parent directories |
| `edit` | Surgical string replacement in files |
| `shell` | Execute shell commands with timeout |
| `glob` | Find files matching glob patterns |
| `grep` | Search file contents with regex (uses ripgrep if available) |
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
| `question` | Ask the user a clarifying question mid-task |
| `task` | Delegate work to a background subagent |
| `create_skill` | Create new skills for repetitive workflows |
| `create_tool` | Create custom Python tools |

### Skills

Skills are reusable, guided workflows that extend CodeAssist's capabilities. They're markdown files with frontmatter that define specialized instructions for specific tasks.

**Using skills:** Type the slash command (e.g., `/review`, `/music`) in chat to invoke a skill. You can also mention the skill by name naturally (e.g., "review this code" or "help me debug this").

**Built-in coding skills:**

| Skill | Slash | Purpose |
|-------|-------|---------|
| `code-review` | `/review` | Review code for bugs, security, and quality |
| `refactor` | `/refactor` | Improve code structure and readability |
| `debug` | `/debug` | Systematic debugging workflow |
| `test` | `/test` | Write unit and integration tests |
| `explain` | `/explain` | Explain how code works |
| `document` | `/doc` | Generate docstrings and documentation |
| `optimize` | `/optimize` | Improve performance |
| `clean` | `/clean` | Remove dead code, organize imports |
| `security` | `/security` | Audit for vulnerabilities |
| `convert` | `/convert` | Convert between languages/frameworks |
| `generate` | `/generate` | Generate boilerplate code |
| `migrate` | `/migrate` | Assist with framework/version upgrades |
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
├── __main__.py         # CLI entry point
├── server.py           # FastAPI web server
├── agent.py            # Agent loop (prompt -> tool calls -> repeat)
├── llm.py              # OpenAI-compatible streaming client
├── config.py           # Configuration loading
├── prompts.py          # System prompt construction
├── session.py          # SQLite session persistence
├── knowledge.py        # Knowledge base CRUD and search
├── embeddings.py       # Vector embeddings for semantic search
├── tools/              # Tool implementations
│   ├── read.py         # Read file contents
│   ├── write.py        # Write file contents
│   ├── edit.py         # Surgical string replacement
│   ├── shell.py        # Execute shell commands
│   ├── glob.py         # Find files by pattern
│   ├── grep.py         # Search file contents
│   ├── webfetch.py     # Fetch web content
│   ├── todo.py         # Task list management
│   ├── git.py          # Git operations
│   ├── fossil.py       # Fossil VCS operations
│   ├── database.py     # SQLite queries
│   ├── directory.py    # Directory listing
│   ├── apply_patch.py  # Unified diff patches
│   ├── documentation.py# Source code documentation
│   ├── http.py         # HTTP requests
│   ├── process.py      # Background process management
│   └── advanced.py     # Web search, user questions, subtasks
├── .codeassist/        # Skills and plugins
│   └── skills/         # Skill markdown files
├── static/             # Web UI
├── Dockerfile          # Container image definition
├── docker-compose.yml  # One-command Docker startup
├── config.toml         # Your config (gitignored)
├── config.example.toml # Config template
└── config.docker.toml  # Config template for Docker
```

## Requirements

- Python 3.11+
- An OpenAI-compatible API (OpenAI, llama.cpp, vLLM, etc.)
- Git (recommended, for repository operations)

A couple of screenshots of it in action.

<img width="1272" height="929" alt="image" src="https://github.com/user-attachments/assets/4be2ae04-85a8-40ef-b0f8-fad343c2105b" />
<img width="844" height="624" alt="image" src="https://github.com/user-attachments/assets/699085b7-b580-4fbc-b34a-c157a8134ab7" />
<img width="831" height="411" alt="image" src="https://github.com/user-attachments/assets/cb468233-6407-4c73-b775-46a32fc54d02" />
<img width="786" height="394" alt="image" src="https://github.com/user-attachments/assets/c778bf52-9c31-4982-8cb5-84ebbb844c2a" />



## License

MIT
