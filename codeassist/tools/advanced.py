import asyncio
import json
import logging
from typing import Any

import httpx

from . import Tool, ToolResult

log = logging.getLogger(__name__)


class WebSearchTool(Tool):
    name = "websearch"
    description = (
        "Search the web for information. Returns search results with titles, URLs, and snippets. "
        "Use this to find documentation, troubleshoot errors, or gather information."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query",
            },
            "num_results": {
                "type": "integer",
                "description": "Number of results to return (default: 10, max: 20)",
            },
        },
        "required": ["query"],
    }

    def __init__(self):
        self.max_chars = 30000
        self._search_engine = "duckduckgo"  # Default search engine

    async def execute(self, query: str, num_results: int = 10) -> ToolResult:
        try:
            num_results = min(max(num_results, 1), 20)

            if self._search_engine == "duckduckgo":
                return await self._search_duckduckgo(query, num_results)
            else:
                return await self._search_generic(query, num_results)

        except Exception as e:
            log.exception("Web search failed")
            return ToolResult(output=f"Web search error: {e}", error=True)

    async def _search_duckduckgo(self, query: str, num_results: int) -> ToolResult:
        """Search using DuckDuckGo (no API key required)."""
        try:
            from duckduckgo_search import DDGS

            with DDGS() as ddgs:
                results = list(ddgs.text(query, max_results=num_results))

            if not results:
                return ToolResult(output="No search results found.")

            output_lines = [f"**Search Results for: {query}**\n"]
            for i, result in enumerate(results, 1):
                title = result.get("title", "No title")
                url = result.get("href", "")
                snippet = result.get("body", "")

                output_lines.append(f"{i}. **{title}**")
                if url:
                    output_lines.append(f"   {url}")
                if snippet:
                    output_lines.append(f"   {snippet}")
                output_lines.append("")

            return ToolResult(output="\n".join(output_lines))

        except ImportError:
            # Fallback to generic search if duckduckgo_search not installed
            return await self._search_generic(query, num_results)

    async def _search_generic(self, query: str, num_results: int) -> ToolResult:
        """Generic web search using HTML scraping of search results."""
        import re
        search_url = f"https://html.duckduckgo.com/html/?q={query.replace(' ', '+')}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(search_url, headers=headers, follow_redirects=True)
                resp.raise_for_status()

            results = []
            # Simple regex extraction of result links from DDG HTML
            for match in re.finditer(
                r'<a[^>]+class="result__a"[^>]*href="([^"]+)"[^>]*>([^<]+)</a>',
                resp.text,
            ):
                results.append({"title": match.group(2), "url": match.group(1)})
                if len(results) >= num_results:
                    break

            if not results:
                # Fallback: try extracting from SERP snippet
                for match in re.finditer(
                    r'class="result__snippet"[^>]*>([^<]+)',
                    resp.text,
                ):
                    pass
                return ToolResult(
                    output=f"Web search for '{query}'\n\nNo structured results found. "
                           f"The duckduckgo_search package provides better results.\n"
                           f"pip install duckduckgo_search"
                )

            output_lines = [f"**Search Results for: {query}**\n"]
            for i, r in enumerate(results, 1):
                output_lines.append(f"{i}. **{r['title']}**")
                output_lines.append(f"   {r['url']}")
                output_lines.append("")

            return ToolResult(output="\n".join(output_lines))

        except Exception as e:
            return ToolResult(
                output=f"Web search for '{query}' failed: {e}\n\n"
                       f"Install 'duckduckgo_search' package for a more reliable search backend:\n"
                       f"pip install duckduckgo_search"
            )


class QuestionTool(Tool):
    name = "question"
    description = (
        "Ask the user structured questions and wait for their response. Supports multiple "
        "questions at once, each with optional multiple-choice options. Use this when you need "
        "clarification or additional information from the user before proceeding.\n\n"
        "Each question can have:\n"
        "- A short header label\n"
        "- Multiple-choice options (label + description)\n"
        "- Allow multiple selections\n"
        "- Custom text answer (always available)\n\n"
        "The 'questions' parameter is an array of question objects. If you only need a single "
        "simple question, use the legacy 'question' string parameter instead."
    )
    parameters = {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "[Legacy] Single question text. Use 'questions' array for structured questions.",
            },
            "questions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "question": {
                            "type": "string",
                            "description": "The question text",
                        },
                        "header": {
                            "type": "string",
                            "description": "Short label (max 30 chars)",
                        },
                        "options": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "label": {"type": "string"},
                                    "description": {"type": "string"},
                                },
                                "required": ["label", "description"],
                            },
                            "description": "Multiple-choice options",
                        },
                        "multiple": {
                            "type": "boolean",
                            "description": "Allow selecting multiple options",
                        },
                    },
                    "required": ["question", "header"],
                },
                "description": "Array of structured questions",
            },
            "options": {
                "type": "array",
                "items": {"type": "string"},
                "description": "[Legacy] Optional list of allowed responses for single question",
            },
            "required": {
                "type": "boolean",
                "description": "Whether the question is required to proceed",
            },
        },
        "required": [],
    }

    def __init__(self):
        self._pending_questions: dict[str, asyncio.Event] = {}
        self._answers: dict[str, str] = {}
        self._question_data: dict[str, dict] = {}

    async def execute(
        self,
        question: str | None = None,
        questions: list[dict] | None = None,
        options: list[str] | None = None,
        required: bool = False,
    ) -> ToolResult:
        import uuid

        question_id = str(uuid.uuid4())[:8]
        self._pending_questions[question_id] = asyncio.Event()

        # Store question metadata for persistence and UI rendering
        data = {
            "id": question_id,
            "required": required,
        }

        if questions:
            data["mode"] = "structured"
            data["questions"] = questions
        elif question:
            data["mode"] = "legacy"
            data["question"] = question
            data["options"] = options
        else:
            data["mode"] = "legacy"
            data["question"] = ""
            data["options"] = None

        self._question_data[question_id] = data

        # Persist to database
        try:
            await self._persist_question(question_id, data)
        except Exception:
            pass

        # Wait for answer
        await self._pending_questions[question_id].wait()
        answer = self._answers.pop(question_id, None)
        self._pending_questions.pop(question_id, None)

        # Update persistence with answer
        if answer is not None:
            try:
                await self._update_question_answer(question_id, answer)
            except Exception:
                pass

        if answer is None:
            return ToolResult(output="No answer received.", error=not required)

        # Format answer based on mode
        if data["mode"] == "structured":
            return ToolResult(output=self._format_structured_answer(data["questions"], answer))
        return ToolResult(output=answer)

    async def _persist_question(self, question_id: str, data: dict):
        """Persist question to database."""
        from codeassist.session import get_db
        import json as j

        q_list = data.get("questions", [{"question": data.get("question", ""), "header": ""}])
        async with get_db() as db:
            await db.execute(
                "INSERT INTO questions (id, session_id, questions, status, created_at) VALUES (?, ?, ?, 'pending', ?)",
                (question_id, "", j.dumps(q_list), datetime_now()),
            )
            await db.commit()

    async def _update_question_answer(self, question_id: str, answer: str):
        """Update question with answer in database."""
        from codeassist.session import get_db
        async with get_db() as db:
            await db.execute(
                "UPDATE questions SET answers = ?, status = 'answered' WHERE id = ?",
                (answer, question_id),
            )
            await db.commit()

    def _format_structured_answer(self, questions: list[dict], answers_raw: str) -> str:
        """Format structured answers as: 'header'='answer1, answer2' per question."""
        lines = []
        try:
            answers = json.loads(answers_raw) if isinstance(answers_raw, str) else answers_raw
            if isinstance(answers, list):
                for i, q in enumerate(questions):
                    header = q.get("header", f"Q{i+1}")
                    ans = answers[i] if i < len(answers) else ""
                    if isinstance(ans, list):
                        lines.append(f"{header}={', '.join(ans)}")
                    else:
                        lines.append(f"{header}={ans}")
            else:
                lines.append(answers_raw)
        except (json.JSONDecodeError, TypeError):
            lines.append(answers_raw)
        return "\n".join(lines)

    def set_answer(self, question_id: str, answer: str):
        """Set the answer to a pending question."""
        self._answers[question_id] = answer
        if question_id in self._pending_questions:
            self._pending_questions[question_id].set()

    def reject_question(self, question_id: str):
        """Reject/dismiss a pending question."""
        self._answers[question_id] = ""
        if question_id in self._pending_questions:
            self._pending_questions[question_id].set()
        # Update persistence
        try:
            import asyncio
            asyncio.create_task(self._reject_question_async(question_id))
        except Exception:
            pass

    async def _reject_question_async(self, question_id: str):
        """Persist rejection to database."""
        from codeassist.session import get_db
        async with get_db() as db:
            await db.execute(
                "UPDATE questions SET status = 'rejected' WHERE id = ?",
                (question_id,),
            )
            await db.commit()


def datetime_now() -> str:
    """Return current UTC timestamp as ISO string."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


class TaskTool(Tool):
    name = "task"
    description = (
        "Spawn a subagent to execute a task. Supports foreground (wait for result) "
        "and background (fire-and-forget with notification) modes.\n\n"
        "Subagent types:\n"
        "- **explore**: Fast, read-only codebase exploration. Use glob, grep, read only.\n"
        "- **general**: Multi-step task execution with full tool access (except task/todowrite).\n"
        "- **build**: Full-access agent for complex implementation tasks.\n\n"
        "In foreground mode, the tool waits for the subagent to complete and returns its result. "
        "In background mode, the tool returns immediately with a task ID; the parent agent will "
        "receive a notification when the subagent completes.\n\n"
        "Use `task_id` parameter to resume an existing task session."
    )
    parameters = {
        "type": "object",
        "properties": {
            "description": {
                "type": "string",
                "description": "Short description (3-5 words) of what the subagent should do",
            },
            "prompt": {
                "type": "string",
                "description": "Detailed instructions for the subagent. Be specific about what to explore, implement, or analyze.",
            },
            "subagent_type": {
                "type": "string",
                "enum": ["explore", "general", "build"],
                "description": "Type of subagent to spawn. 'explore' is read-only and fast. 'general' has full tool access. 'build' is for complex implementation.",
            },
            "task_id": {
                "type": "string",
                "description": "Existing task ID to resume. If not provided, a new task is created.",
            },
            "background": {
                "type": "boolean",
                "description": "If true, run in background and return immediately. Parent will be notified on completion. Default: false (foreground).",
            },
        },
        "required": ["description", "prompt", "subagent_type"],
    }

    def __init__(self):
        self._parent_session_id = ""
        self._config = None
        self._tools_registry = None

    def configure(self, session_id: str, config=None, tools_registry=None):
        """Configure the task tool with session context."""
        self._parent_session_id = session_id
        self._config = config
        self._tools_registry = tools_registry

    async def execute(
        self,
        description: str,
        prompt: str,
        subagent_type: str,
        task_id: str | None = None,
        background: bool = False,
    ) -> ToolResult:
        from codeassist.subagent import subagent_manager

        # Validate config
        if not self._config:
            return ToolResult(output="Error: TaskTool not configured. Subagents require server context.", error=True)

        # Check depth limit
        max_depth = self._config.agent.subagent_depth
        if max_depth <= 0:
            return ToolResult(output="Error: Subagents are disabled (subagent_depth=0).", error=True)

        # Create or resume task
        task = await subagent_manager.create_task(
            description=description,
            prompt=prompt,
            subagent_type=subagent_type,
            parent_session_id=self._parent_session_id,
            background=background,
            task_id=task_id,
        )

        if background:
            # Background mode: start async and return immediately
            async def _run_background():
                try:
                    await subagent_manager.start_task(
                        task.id,
                        self._agent_runner,
                        self._config,
                        self._tools_registry,
                        depth=0,
                    )
                except Exception as e:
                    log.exception("Background subagent %s failed", task.id)

            asyncio.create_task(_run_background())

            return ToolResult(
                output=f'<task id="{task.id}" state="running">\n'
                       f'Subagent "{subagent_type}" started in background.\n'
                       f'Description: {description}\n'
                       f'The parent agent will be notified when this task completes.\n'
                       f'</task>'
            )
        else:
            # Foreground mode: wait for result
            try:
                result = await subagent_manager.start_task(
                    task.id,
                    self._agent_runner,
                    self._config,
                    self._tools_registry,
                    depth=0,
                )

                status = "completed" if task.status == "completed" else "error"
                return ToolResult(
                    output=f'<task id="{task.id}" state="{status}">\n'
                           f'{result}\n'
                           f'</task>'
                )
            except Exception as e:
                return ToolResult(
                    output=f'<task id="{task.id}" state="error">\n'
                           f'Error running subagent: {e}\n'
                           f'</task>',
                    error=True,
                )

    def _agent_runner(self, agent, prompt):
        """Default agent runner — runs the agent loop."""
        return agent.run(prompt)

    def get_task_status(self, task_id: str) -> dict | None:
        """Get task status."""
        from codeassist.subagent import subagent_manager
        task = subagent_manager.get_task(task_id)
        return task.to_dict() if task else None

    def list_tasks(self) -> list[dict]:
        """List all tasks."""
        from codeassist.subagent import subagent_manager
        return subagent_manager.list_tasks()
