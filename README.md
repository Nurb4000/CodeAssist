# CodeAssist

AI coding assistant that runs **locally on your machine**. Connects to OpenAI or any OpenAI-compatible endpoint (llama.cpp, vLLM, etc.) and gives you a web-based chat UI with file editing, shell execution, code search, and more.

## Why local?

CodeAssist is designed to run on your development machine, not a remote server. This means:

- **Tools access your local filesystem directly** -- no file sync, no daemons
- **Shell commands run on your machine** -- your env, your tools, your dependencies
- **No network latency** -- everything runs on localhost
- **Your code never leaves your machine** (except to the LLM API you configure)

## Quick Start

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

# Run
codeassist
```

### With venv

```bash
python -m venv .venv
source .venv/bin/activate

pip install -e .

cp config.example.toml config.toml
# Edit config.toml

codeassist
```

### Without installing

```bash
python -m codeassist
```

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

See `config.example.toml` for all options.

## Usage

```bash
# Default -- opens browser at http://127.0.0.1:8000
codeassist

# Custom port
codeassist --port 9000

# Custom workspace
codeassist --workspace ~/my-project

# Don't auto-open browser
codeassist --no-browser
```

## What it does

- **Chat with an AI** that can read, write, and edit your code
- **Run shell commands** through the chat (build, test, git, etc.)
- **Search code** with regex patterns
- **Find files** with glob patterns
- **Fetch web content** for documentation lookup
- **Track tasks** across multi-step work
- **Continue** conversations with context

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
│   └── todo.py         # Task list management
├── static/             # Web UI
├── config.toml         # Your config (gitignored)
└── config.example.toml # Config template
```

## Requirements

- Python 3.11+
- An OpenAI-compatible API (OpenAI, llama.cpp, vLLM, etc.)

## License

MIT
