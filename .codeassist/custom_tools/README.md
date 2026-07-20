# Custom Tools Directory

This directory contains auto-generated and user-created tools for CodeAssist.

## Tool Structure

Each tool is a Python file with a `TOOLS` dictionary export:

```python
"""Tool description."""

TOOLS = {
    "tool_name": {
        "name": "tool_name",
        "description": "What this tool does",
        "parameters": {
            "type": "object",
            "properties": {
                "param1": {
                    "type": "string",
                    "description": "Parameter description"
                }
            },
            "required": ["param1"]
        }
    }
}

async def execute(param1: str) -> str:
    """Execute the tool."""
    return f"Result: {param1}"
```

## Safety

- Tools in this directory are loaded dynamically
- Custom tools require confirmation before execution (unless marked as trusted)
- Maximum 10 custom tools per project
- All tool executions are logged to the knowledge base

## Auto-Creation

CodeAssist can automatically create tools when it detects repetitive patterns.
This feature is controlled by the `auto_create_tools` config option (disabled by default).

To enable:
```toml
[agent]
auto_create_tools = true
```
