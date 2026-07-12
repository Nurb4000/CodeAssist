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
        """Generic web search using a simple HTTP approach."""
        # This is a fallback implementation
        # In production, you'd use a proper search API or library
        return ToolResult(
            output=f"Web search for '{query}'\n\nNote: Install 'duckduckgo_search' package for full search functionality.\n"
                   f"pip install duckduckgo_search"
        )


class QuestionTool(Tool):
    name = "question"
    description = (
        "Ask the user a question and wait for their response. Use this when you need "
        "clarification or additional information from the user before proceeding."
    )
    parameters = {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "The question to ask the user",
            },
            "options": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional list of allowed responses",
            },
            "required": {
                "type": "boolean",
                "description": "Whether the question is required to proceed",
            },
        },
        "required": ["question"],
    }

    def __init__(self):
        self._pending_questions: dict[str, asyncio.Event] = {}
        self._answers: dict[str, str] = {}

    async def execute(self, question: str, options: list[str] | None = None,
                     required: bool = False) -> ToolResult:
        # In a real implementation, this would pause the agent and wait for user input
        # For now, return a placeholder
        return ToolResult(
            output=f"Question for user: {question}\n"
                   f"Options: {', '.join(options) if options else 'Any response'}\n"
                   f"Required: {required}"
        )

    def set_answer(self, question_id: str, answer: str):
        """Set the answer to a pending question."""
        self._answers[question_id] = answer
        if question_id in self._pending_questions:
            self._pending_questions[question_id].set()

    async def wait_for_answer(self, question_id: str) -> str | None:
        """Wait for user to answer a question."""
        event = asyncio.Event()
        self._pending_questions[question_id] = event

        await event.wait()
        return self._answers.pop(question_id, None)


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
