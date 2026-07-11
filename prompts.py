import platform
import sys
from datetime import date
from pathlib import Path


BASE_PROMPT = """You are CodeAssist, an AI coding agent. You help developers write, edit, debug, and understand code.

You have access to tools that let you read files, write files, edit files, run shell commands, search code, and fetch web content.

## Guidelines
- Be concise and direct
- Read files before editing them to understand context
- Make targeted edits rather than rewriting entire files
- Verify your work when possible (run tests, check syntax)
- Never commit secrets, keys, or credentials
- Follow existing code conventions in the project
- Explain non-obvious changes briefly

## Tool Usage
- Use `read` to examine files before modifying them
- Use `edit` for surgical string replacements (preferred over write)
- Use `write` only for new files or complete rewrites
- Use `shell` to run commands, tests, build tools, etc.
- Use `glob` to find files by pattern
- Use `grep` to search file contents
- Use `webfetch` to retrieve web content for research
- Use `todo` to track multi-step tasks"""

TOOL_INSTRUCTIONS = """## Important Tool Rules
- Always use absolute file paths
- When using `edit`, provide the exact string to find including surrounding context to avoid ambiguity
- Check `edit` results for errors (multiple matches, not found, etc.)
- For shell commands, prefer `&&` chaining over separate calls
- Use timeout parameter for long-running commands"""


def build_system_prompt(workspace: Path, model_id: str) -> str:
    env_block = f"""<env>
  Working directory: {workspace}
  Platform: {sys.platform}
  Python: {sys.version.split()[0]}
  Model: {model_id}
  Today's date: {date.today().isoformat()}
</env>"""

    parts = [BASE_PROMPT, env_block, TOOL_INSTRUCTIONS]
    return "\n\n".join(parts)


def build_openai_messages(system_prompt: str, history: list[dict]) -> list[dict]:
    messages = [{"role": "system", "content": system_prompt}]

    for msg in history:
        role = msg["role"]

        if role == "user":
            messages.append({"role": "user", "content": msg["content"]})

        elif role == "assistant":
            entry: dict = {"role": "assistant", "content": msg.get("content") or ""}
            if msg.get("tool_calls"):
                entry["tool_calls"] = json.loads(msg["tool_calls"]) if isinstance(msg["tool_calls"], str) else msg["tool_calls"]
                if not entry["content"]:
                    entry["content"] = None
            messages.append(entry)

        elif role == "tool":
            messages.append({
                "role": "tool",
                "tool_call_id": msg["tool_call_id"],
                "content": msg.get("content") or "",
            })

    return messages


import json
