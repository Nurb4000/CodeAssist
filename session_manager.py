import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from session import Session


class SessionManager:
    """Manages session operations like fork, export, import."""

    @staticmethod
    async def fork_session(session_id: str, name: str | None = None) -> Session:
        """Create a fork of an existing session."""
        original = Session(session_id)
        return await original.fork(name)

    @staticmethod
    async def export_session(session_id: str, redact: bool = False) -> dict:
        """Export session data for sharing or backup."""
        session = Session(session_id)
        messages = await session.get_messages()

        export_data = {
            "version": 1,
            "session_id": session_id,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "messages": messages,
        }

        if redact:
            export_data = SessionManager._redact_pii(export_data)

        return export_data

    @staticmethod
    def _redact_pii(data: dict) -> dict:
        """Redact potentially sensitive information from export data."""
        pii_patterns = [
            r'(?i)(api[_-]?key|secret|password|token)\s*[:=]\s*["\']?[\w\-]+["\']?',
            r'sk-[a-zA-Z0-9]{10,}',
            r'eyJ[a-zA-Z0-9_-]*\.eyJ[a-zA-Z0-9_-]*\.[a-zA-Z0-9_-]*',
        ]

        redacted_messages = []
        for msg in data.get("messages", []):
            content = msg.get("content", "") or ""
            for pattern in pii_patterns:
                content = re.sub(pattern, "***REDACTED***", content)
            msg["content"] = content
            redacted_messages.append(msg)

        data["messages"] = redacted_messages
        return data

    @staticmethod
    async def import_session(export_data: dict, name: str | None = None) -> Session:
        """Import session data from export."""
        new_session = await Session.create(name=name or "Imported Session")

        for msg in export_data.get("messages", []):
            await new_session.add_message(
                role=msg["role"],
                content=msg.get("content"),
                tool_calls=json.loads(msg["tool_calls"]) if msg.get("tool_calls") else None,
                tool_call_id=msg.get("tool_call_id"),
                name=msg.get("name"),
            )

        return new_session

    @staticmethod
    async def get_session_summary(session_id: str) -> dict:
        """Get a summary of a session for display."""
        session = Session(session_id)
        messages = await session.get_messages()

        user_messages = [m for m in messages if m["role"] == "user"]
        assistant_messages = [m for m in messages if m["role"] == "assistant"]
        tool_messages = [m for m in messages if m["role"] == "tool"]

        return {
            "id": session_id,
            "message_count": len(messages),
            "user_message_count": len(user_messages),
            "assistant_message_count": len(assistant_messages),
            "tool_call_count": len(tool_messages),
            "first_message": user_messages[0]["content"][:100] if user_messages else None,
            "last_message": user_messages[-1]["content"][:100] if user_messages else None,
        }


class SessionTool:
    """Tool for session management operations."""

    name = "session"
    description = (
        "Manage sessions: fork, export, import, or get summary. "
        "Use 'fork' to create a copy of the current session, "
        "'export' to export session data, 'import' to import from JSON, "
        "or 'summary' to get session statistics."
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["fork", "export", "import", "summary"],
                "description": "Session action to perform",
            },
            "session_id": {
                "type": "string",
                "description": "Session ID (for fork, export, import)",
            },
            "name": {
                "type": "string",
                "description": "Name for new session (for fork, import)",
            },
            "data": {
                "type": "string",
                "description": "JSON data to import (for import action)",
            },
            "redact": {
                "type": "boolean",
                "description": "Redact PII in export (for export action)",
            },
        },
        "required": ["action"],
    }

    def __init__(self, current_session_id: str):
        self.current_session_id = current_session_id

    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }

    async def execute(self, action: str, session_id: str | None = None,
                     name: str | None = None, data: str | None = None,
                     redact: bool = False) -> str:
        if action == "fork":
            target_id = session_id or self.current_session_id
            try:
                new_session = await SessionManager.fork_session(target_id, name)
                return f"Forked session. New session ID: {new_session.id}"
            except Exception as e:
                return f"Error forking session: {e}"

        elif action == "export":
            target_id = session_id or self.current_session_id
            try:
                export_data = await SessionManager.export_session(target_id, redact)
                return json.dumps(export_data, indent=2)
            except Exception as e:
                return f"Error exporting session: {e}"

        elif action == "import":
            if not data:
                return "Error: data (JSON string) is required for import"
            try:
                import_data = json.loads(data)
                new_session = await SessionManager.import_session(import_data, name)
                return f"Imported session. New session ID: {new_session.id}"
            except json.JSONDecodeError as e:
                return f"Error: invalid JSON data: {e}"
            except Exception as e:
                return f"Error importing session: {e}"

        elif action == "summary":
            target_id = session_id or self.current_session_id
            try:
                summary = await SessionManager.get_session_summary(target_id)
                return json.dumps(summary, indent=2)
            except Exception as e:
                return f"Error getting session summary: {e}"

        else:
            return f"Error: unknown action '{action}'"
