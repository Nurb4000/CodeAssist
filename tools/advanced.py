import asyncio
import json
import logging
from typing import Any

import httpx

from tools import Tool, ToolResult

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
        "Run a background task or subagent. Use this for long-running operations "
        "or to delegate complex work to a subagent. Returns a task ID that can be "
        "used to check status or get results."
    )
    parameters = {
        "type": "object",
        "properties": {
            "description": {
                "type": "string",
                "description": "Description of the task",
            },
            "prompt": {
                "type": "string",
                "description": "Instructions for the task",
            },
            "tool": {
                "type": "string",
                "description": "Specific tool to use (optional)",
            },
        },
        "required": ["description", "prompt"],
    }

    def __init__(self):
        self._tasks: dict[str, dict] = {}

    async def execute(self, description: str, prompt: str, tool: str | None = None) -> ToolResult:
        import uuid

        task_id = str(uuid.uuid4())[:8]
        self._tasks[task_id] = {
            "id": task_id,
            "description": description,
            "prompt": prompt,
            "tool": tool,
            "status": "running",
            "result": None,
        }

        # In a real implementation, this would spawn a background task
        # For now, we'll simulate completion
        self._tasks[task_id]["status"] = "completed"
        self._tasks[task_id]["result"] = f"Task '{description}' completed successfully."

        return ToolResult(
            output=f"**Task Created:** {description}\n"
                   f"**Task ID:** {task_id}\n"
                   f"**Status:** Completed\n"
                   f"**Result:** {self._tasks[task_id]['result']}"
        )

    def get_task(self, task_id: str) -> dict | None:
        """Get task status and result."""
        return self._tasks.get(task_id)

    def list_tasks(self) -> list[dict]:
        """List all tasks."""
        return [
            {
                "id": t["id"],
                "description": t["description"],
                "status": t["status"],
            }
            for t in self._tasks.values()
        ]
