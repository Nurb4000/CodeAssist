import asyncio
import json
import logging
from pathlib import Path

import aiosqlite

from tools import Tool, ToolResult

log = logging.getLogger(__name__)


class DatabaseTool(Tool):
    name = "database"
    description = (
        "Execute SQL queries against SQLite databases. "
        "Use this to query, insert, update, or delete data from SQLite databases. "
        "Supports both read and write operations."
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["query", "insert", "update", "delete", "schema", "tables"],
                "description": "Database operation to perform",
            },
            "db_path": {
                "type": "string",
                "description": "Path to SQLite database file",
            },
            "sql": {
                "type": "string",
                "description": "SQL query to execute",
            },
            "params": {
                "type": "array",
                "description": "Query parameters (for parameterized queries)",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of rows to return (default: 100)",
            },
        },
        "required": ["action", "sql"],
    }

    def __init__(self):
        self.max_rows = 100
        self.max_output_chars = 50000

    async def execute(self, action: str, sql: str, db_path: str | None = None,
                     params: list | None = None, limit: int | None = None) -> ToolResult:
        try:
            # Default to CodeAssist's own database if not specified
            if not db_path:
                from session import DB_PATH
                db_path = str(DB_PATH)

            # Validate database exists
            if not Path(db_path).exists():
                return ToolResult(output=f"Error: Database file not found: {db_path}", error=True)

            if action == "query":
                return await self._query(db_path, sql, params, limit)
            elif action == "insert":
                return await self._write(db_path, sql, params, "INSERT")
            elif action == "update":
                return await self._write(db_path, sql, params, "UPDATE")
            elif action == "delete":
                return await self._write(db_path, sql, params, "DELETE")
            elif action == "schema":
                return await self._get_schema(db_path, sql)
            elif action == "tables":
                return await self._list_tables(db_path)
            else:
                return ToolResult(output=f"Error: unknown action '{action}'", error=True)

        except Exception as e:
            log.exception("Database operation failed")
            return ToolResult(output=f"Database error: {e}", error=True)

    async def _query(self, db_path: str, sql: str, params: list | None, limit: int | None) -> ToolResult:
        """Execute a SELECT query."""
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(sql, params or [])
            
            if cursor.description:
                columns = [desc[0] for desc in cursor.description]
                rows = await cursor.fetchmany(limit or self.max_rows)
                
                result = {
                    "columns": columns,
                    "rows": [dict(row) for row in rows],
                    "row_count": len(rows),
                }
                
                return ToolResult(output=json.dumps(result, indent=2, default=str))
            else:
                return ToolResult(output="Query executed successfully (no results)")

    async def _write(self, db_path: str, sql: str, params: list | None, operation: str) -> ToolResult:
        """Execute an INSERT, UPDATE, or DELETE statement."""
        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute(sql, params or [])
            await db.commit()
            
            return ToolResult(
                output=f"{operation} successful. Rows affected: {cursor.rowcount}"
            )

    async def _get_schema(self, db_path: str, table_name: str) -> ToolResult:
        """Get schema information for a table."""
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row

            # Validate table name against sqlite_master to prevent SQL injection
            cursor = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table_name,),
            )
            if not await cursor.fetchone():
                return ToolResult(output=f"Error: Table '{table_name}' not found", error=True)

            # Get table info
            cursor = await db.execute(f"PRAGMA table_info({table_name})")
            columns = await cursor.fetchall()
            
            schema = {
                "table": table_name,
                "columns": []
            }
            
            for col in columns:
                schema["columns"].append({
                    "cid": col[0],
                    "name": col[1],
                    "type": col[2],
                    "notnull": bool(col[3]),
                    "default_value": col[4],
                    "pk": bool(col[5]),
                })
            
            return ToolResult(output=json.dumps(schema, indent=2))

    async def _list_tables(self, db_path: str) -> ToolResult:
        """List all tables in the database."""
        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
            tables = await cursor.fetchall()
            
            table_list = [row[0] for row in tables]
            
            return ToolResult(
                output=f"**Tables in database:**\n" + 
                       "\n".join(f"- {t}" for t in table_list)
            )
