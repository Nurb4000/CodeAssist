# Contributing to CodeAssist

Thank you for your interest in contributing to CodeAssist! This guide covers how to set up a development environment, run tests, and submit contributions.

## Development Setup

1. **Clone the repository:**
   ```bash
   git clone <repo-url>
   cd CodeAssist
   ```

2. **Create a virtual environment:**
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # Linux/macOS
   # .venv\Scripts\activate   # Windows
   ```

3. **Install dependencies:**
   ```bash
   pip install -e ".[dev]"
   ```

4. **Copy and configure:**
   ```bash
   cp config.example.toml config.toml
   # Edit config.toml with your LLM API key or endpoint
   ```

## Project Structure

```
codeassist/          # Core application package
├── agent.py         # Agent loop (prompt → tool calls → execute)
├── server.py        # FastAPI app, WebSocket endpoint
├── llm.py           # OpenAI-compatible streaming client
├── config.py        # TOML configuration loading
├── session.py       # SQLite persistence layer
├── routes/          # REST API endpoints
tools/               # Tool implementations (read, write, edit, shell, etc.)
tests/               # Test suite
codeassist/skills/  # Built-in skill definitions
codeassist/static/   # Web UI assets
```

## Running the Server

```bash
codeassist --workspace /path/to/your/project
# or
python -m codeassist --workspace /path/to/your/project
```

The server starts at `http://localhost:8090` by default. API docs are available at `http://localhost:8090/docs`.

## Running Tests

```bash
# Run all tests
./run_tests.sh

# Or with pytest directly
pytest tests/ -v

# Run with coverage
pytest tests/ --cov=codeassist --cov=tools --cov-report=term-missing
```

Tests use a separate SQLite database (`data/codeassist_test.db`) and isolated temp directories. The `clean_database` fixture ensures DB isolation between tests.

## Adding a New Tool

1. Create a new file in `tools/` (e.g., `tools/my_tool.py`).
2. Subclass `Tool` from `tools/__init__.py`:
   ```python
   from tools import Tool, ToolResult
   
   class MyTool(Tool):
       name = "my_tool"
       description = "What this tool does"
       workspace = Path(".")
       
       parameters = {
           "type": "object",
           "properties": {
               "input": {"type": "string", "description": "Input description"},
           },
           "required": ["input"],
       }
       
       async def execute(self, input: str) -> ToolResult:
           # Your implementation
           return ToolResult(output=f"Processed: {input}")
   ```
3. Register it in `tools/__init__.py`'s `create_registry()` function.
4. Add tests in `tests/test_tools/`.

## Adding a New Skill

1. Create a markdown file in `runtime/skills/` with YAML frontmatter:
   ```markdown
   ---
   name: my-skill
   description: What this skill does
   slash: mycommand
   ---
   
   # My Skill
   
   Instructions here...
   ```
2. The skill is auto-discovered on server start or via `POST /api/skills/reload`.

## Code Style

- Follow PEP 8 with type hints
- Use async/await consistently (all I/O is async)
- Keep functions focused and small
- Add docstrings to public functions and API routes
- Use `logging.getLogger(__name__)` instead of `print()`

## Pull Request Process

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/my-feature`)
3. Make your changes with tests
4. Run the test suite (`pytest tests/ -v`)
5. Commit with clear, descriptive messages
6. Push and open a Pull Request

## Issue Guidelines

When reporting issues, please include:
- CodeAssist version
- LLM provider and model used
- Steps to reproduce
- Expected vs actual behavior
- Relevant log output (set `log_level` to DEBUG in config)

## Architecture Notes

- **Agent Loop** (`agent.py`): Core iteration loop that streams LLM responses, handles tool calls, manages confirmations, and enforces context limits via two-level compaction.
- **Tool Registry** (`tools/__init__.py`): Maps tool names to instances, validates arguments against JSON Schema, executes tools in parallel via `asyncio.gather()`.
- **Session Persistence** (`session.py`): SQLite with aiosqlite, connection pooling, WAL mode, and schema migrations.
- **WebSocket Protocol** (`server.py:websocket_endpoint`): Single WS endpoint streams events (`text_delta`, `tool_call`, `tool_result`, etc.) for real-time chat.
- **Trust Registry** (`trust_registry.py`): SHA-256 hash-based integrity checking for dynamically loaded custom tools and plugins.

## Questions?

Open an issue or discussion on the repository. Pull requests are welcome!
