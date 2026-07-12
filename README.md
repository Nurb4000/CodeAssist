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

## License

MIT
