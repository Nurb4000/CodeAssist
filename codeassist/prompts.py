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
- When the task is complete, say so and stop. Do not re-read, re-check, or re-verify work you have already finished, and do not keep calling tools to confirm what you already know
- Stay focused on finishing the request: research informs your edits, it does not replace them. When asked to implement, fix, add, or build something, always produce the actual code or documentation changes — do not stop after gathering information or conclude with a summary instead of doing the work
- Do NOT summarize your work, explain your process, reflect internally, narrate your actions, or provide a recap. Do not say "Here's what I did", "Let me summarize", "To summarize", "In summary", "Let me continue", "Let me look at", "Let me check", "I need to", "I should", "Now let me", "Let me also", "To complete", or similar self-talk/thought narration. Do not repeat the same statement or phrase. Do not loop - take action once per step. Output only the final result requested by the user. No preamble, no postamble, no self-talk, no thinking aloud.

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

SUMMARY_TEMPLATE = """You are summarizing a conversation so the NEXT turn of this same assistant can continue without losing track. The reader sees only this summary plus recent context, so anything you omit is lost forever. Mimic OpenCode's checkpoint: progress-anchored, terse single-line bullets (not prose), and every section filled or explicitly "(none)".

## Objective
- One or two sentences on what the user is trying to accomplish.

## Requirements
- User-stated constraints, preferences, and scope boundaries, or "(none)".

## Decisions
- Decisions already made and why, or "(none)".

## Work State
Break the objective into smaller goals and report which are completed, being worked on, or blocked.
### Completed
- Goals finished or verified facts; use "(none)" if nothing is done yet.

### Active
- Goals currently in progress, partial changes, or investigation state; use "(none)".

### Blocked
- Anything blocking progress and why; use "(none)".

## Next Move
1. The immediate concrete action to take next (or "(none)").
2. The follow-up action if the first is known (or "(none)").

## Relevant Files
Files and directories, beyond the current working directory, that another agent needs to open to continue (at most 15, most important first); "(none)" if none.

## Important Context
Facts the next agent cannot continue without and cannot easily find on its own -- exact commands run, error strings, URLs, identifiers, and whether changes are uncommitted, committed, pushed, or under review; "(none)".

Rules:
- Preserve exact file paths, symbols, commands, error strings, and URLs.
- Carry forward only user questions or requests that remain unanswered or need further action; drop ones newer history has answered or resolved. Preserve exact wording when carrying one forward.
- When the summary and recent context conflict, the recent context wins: state the corrected fact and drop the old claim.
- Do not mention this summarization process or that context was compacted."""

COMPACTION_USER_PROMPT = """Update the existing checkpoint below into one consolidated summary. Do not continue the task or call tools.

Existing checkpoint (previous summary):
{previous_summary}

--- New conversation turns to summarize ---
{conversation}
--- End of new turns ---

Rules for merging:
- Newer history always takes precedence over the existing checkpoint. Preserve prior information unless newer history clearly contradicts, supersedes, resolves, or makes it stale; if it is no longer relevant to continuing the work, drop it.
- Incorporate the new requirements, decisions, and progress. Reconcile Work State and Next Move: move completed work out of Active, remove resolved blockers and answered questions, and keep only unresolved or pending items.
- Carry forward only user questions or requests that remain unanswered or need further action; do not repeat ones newer history has resolved. Preserve exact wording when carrying one forward.
- Return the updated summary as Markdown sections using the exact headings from SUMMARY_TEMPLATE (fill each section, or "(none)"). Do not include the <conversation-checkpoint>, <summary>, or <recent-context> wrapper tags.
- Do not mention this summarization process or that context was compacted."""

SUMMARY_NUDGE = """The previous response did not fill in the required summary template sections. Do not call tools or continue the task. Return the updated summary as Markdown using the exact section headings from the template, filling each section (or "(none)")."""


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
            reasoning = msg.get("reasoning_content")
            if reasoning:
                entry["reasoning_content"] = reasoning
            if msg.get("tool_calls"):
                entry["tool_calls"] = json.loads(msg["tool_calls"]) if isinstance(msg["tool_calls"], str) else msg["tool_calls"]
                if not entry["content"] and not entry.get("reasoning_content"):
                    entry["content"] = None
            messages.append(entry)

        elif role == "tool":
            messages.append({
                "role": "tool",
                "tool_call_id": msg["tool_call_id"],
                "content": msg.get("content") or "",
            })

    return messages
