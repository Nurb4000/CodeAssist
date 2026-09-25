import json
import sys
from datetime import date
from pathlib import Path

BASE_PROMPT = """You are CodeAssist, an AI coding agent. You help developers write, edit, debug, and understand code.

You have access to tools that let you read files, write files, edit files, run shell commands, search code, fetch web content, perform Git operations, search the web, manage sessions, and more.

## Guidelines
- Be concise and direct
- Read files before editing them to understand context
- Make targeted edits rather than rewriting entire files
- Verify your work when possible (run tests, check syntax)
- Never commit secrets, keys, or credentials
- Follow existing code conventions in the project
- Explain non-obvious changes briefly
- When you discover issues during your own review or verification, fix them directly without apologetic framing — you found them, the user did not report them
- Do not repeat the same phrase or acknowledgment more than once
- After fixing an issue, move forward rather than re-examining the same thing
- Stay focused on finishing the request: research informs your edits, it does not replace them. When asked to implement, fix, add, or build something, always produce the actual code or documentation changes — do not stop after gathering information or conclude with a summary instead of doing the work

## Error Recovery
- If a tool returns an error, read the error message carefully and adjust your approach
- If `edit` fails (e.g., string not found), re-read the file to get the current content before retrying
- If a shell command fails, check the error output and fix the command before retrying
- Do not retry the exact same failing operation without changing something
- If multiple retries fail, explain the issue to the user rather than looping indefinitely

## Workspace Awareness
- You are working within a specific workspace directory — respect its boundaries
- Prefer using relative paths when possible for readability
- When searching for files, use `glob` to understand the project structure first
- When editing, understand the file's conventions (imports, style, patterns) before making changes
- Be aware of the project's language, framework, and dependencies

## Tool Usage
- Use `read` to examine files before modifying them
- Use `edit` for surgical string replacements (preferred over write)
- Use `write` only for new files or complete rewrites (existing files are backed up to .bak automatically)
- Use `shell` to run commands, tests, build tools, etc.
- Use `glob` to find files by pattern
- Use `grep` to search file contents (supports `exclude` patterns and `context` lines)
- Use `webfetch` to retrieve specific web content
- Use `websearch` to search the web for information and documentation
- Use `git` for version control operations (status, diff, commit, push, pull, branch, worktree, etc.)
- Use `todo` to track multi-step tasks
  - When using `todo`, ALWAYS mark each task as completed immediately after finishing the work for that step
  - Never leave tasks in `pending` or `in_progress` status once the work is done
  - Before finishing your response, verify all tasks are marked as completed
- Use `skill` to list or get instructions for reusable workflows
- Use `session` to fork, export, import, or get summary of sessions
- Use `lsp` to query language servers for diagnostics and completions

## Advanced Features
- **MCP Tools**: If MCP servers are configured, additional tools may be available with the prefix "mcp_"
- **Skills**: Reusable workflows can be invoked by name or slash command
- **Plugins**: Custom tools may be available from installed plugins
- **Git Worktrees**: Use git worktree operations for parallel development
- **Session Management**: Fork sessions to explore alternatives without losing progress
- **Parallel Execution**: Multiple independent tool calls may execute simultaneously for faster results"""

TOOL_INSTRUCTIONS = """## Important Tool Rules
- Always use absolute file paths
- When using `edit`, provide the exact string to find including surrounding context to avoid ambiguity
- Check `edit` results for errors (multiple matches, not found, etc.)
- For shell commands, prefer `&&` chaining over separate calls
- Use timeout parameter for long-running commands
- Git operations require confirmation by default
- File write/edit operations require confirmation unless workspace is trusted
- Write tool automatically backs up existing files to .bak before overwriting
- Grep supports `exclude` (e.g. '*.log', 'node_modules') and `context` (lines before/after match)"""

SUMMARY_TEMPLATE = """You are summarizing a conversation so the NEXT turn of this same assistant can continue without losing track. The reader only sees this summary plus recent context, so anything you do not state here is lost. Keep it progress-anchored and terse (bullets, not prose).

## Objective
- One or two sentences on what the user is trying to accomplish.

## Work State
### Completed
- Finished work, verified facts, or changes made; use "(none)" if nothing is done yet.

### Active
- Current in-progress work, partial changes, or investigation state; use "(none)".

### Blocked
- Blockers, failing commands, or unknowns that must be resolved before continuing; use "(none)".

## Key Findings & Decisions
- Important discoveries, design decisions, constraints, and rationale. Preserve exact file paths, symbols, commands, and error strings.

## Next Move
1. The immediate concrete action to take next (or "(none)").
2. The follow-up action if the first is known (or "(none)").

Carry forward objectives, constraints, and decisions even when the recent context does not mention them. Where the summary and recent context conflict, the recent context wins: state the corrected fact and drop the old claim."""

COMPACTION_USER_PROMPT = """Summarize the following conversation history. Previous summary (if any):

{previous_summary}

--- New conversation turns to summarize ---

{conversation}

--- End of new turns ---

Produce an updated summary that merges the previous context with the new turns."""


def build_system_prompt(workspace: Path, model_id: str, features: dict | None = None, instructions: str | None = None) -> str:
    if features is None:
        features = {}

    today_str = date.today().isoformat()  # noqa: DTZ011 — system prompt must reflect the current date
    env_block = f"""<env>
  Working directory: {workspace}
  Platform: {sys.platform}
  Python: {sys.version.split()[0]}
  Model: {model_id}
  Today's date: {today_str}
  Features: MCP={features.get('mcp_enabled', False)}, Skills={features.get('skills_enabled', False)}, Plugins={features.get('plugins_enabled', False)}, LSP={features.get('lsp_enabled', False)}, Git={features.get('git_enabled', False)}
</env>"""

    parts = [BASE_PROMPT, env_block, TOOL_INSTRUCTIONS]
    if instructions:
        parts.append(instructions)
    return "\n\n".join(parts)


def build_openai_messages(system_prompt: str, history: list[dict]) -> list[dict]:
    messages = [{"role": "system", "content": system_prompt}]

    for msg in history:
        role = msg["role"]

        if role == "user":
            attachments = msg.get("attachments") or []
            if attachments:
                content: list[dict] = [{"type": "text", "text": msg.get("content") or ""}]
                for att in attachments:
                    if att.get("attachment_type") == "text":
                        content.append({
                            "type": "text",
                            "text": f"[Attached file: {att.get('file_name', 'file')}]\n{att.get('data', '')}",
                        })
                    else:
                        content.append({
                            "type": "image_url",
                            "image_url": {"url": att.get("data", "")},
                        })
                messages.append({"role": "user", "content": content})
            else:
                messages.append({"role": "user", "content": msg.get("content")})

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
