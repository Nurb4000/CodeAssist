import aiosqlite
import json
import uuid
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass


DB_PATH = Path(__file__).parent.parent / "data" / "codeassist.db"


async def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                name TEXT,
                created_at TEXT,
                updated_at TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,
                session_id TEXT REFERENCES sessions(id),
                role TEXT NOT NULL,
                content TEXT,
                tool_call_id TEXT,
                tool_calls TEXT,
                name TEXT,
                created_at TEXT
            )
        """)
        await db.commit()


class Session:
    def __init__(self, session_id: str):
        self.id = session_id

    @classmethod
    async def create(cls, name: str | None = None) -> "Session":
        sid = str(uuid.uuid4())
        now = datetime.utcnow()
        now_str = now.isoformat()
        default_name = now.strftime("%Y-%m-%d %H:%M")
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT INTO sessions (id, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (sid, name or default_name, now_str, now_str),
            )
            await db.commit()
        return cls(sid)

    @classmethod
    async def list_all(cls) -> list[dict]:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM sessions ORDER BY updated_at DESC")
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    @classmethod
    async def get_or_create_latest(cls) -> "Session":
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT id FROM sessions ORDER BY updated_at DESC LIMIT 1")
            row = await cursor.fetchone()
            if row:
                return cls(row[0])
        return await cls.create()

    async def add_message(
        self,
        role: str,
        content: str | None = None,
        tool_calls: list[dict] | None = None,
        tool_call_id: str | None = None,
        name: str | None = None,
    ) -> str:
        mid = str(uuid.uuid4())
        now = datetime.utcnow().isoformat()
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT INTO messages (id, session_id, role, content, tool_call_id, tool_calls, name, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    mid,
                    self.id,
                    role,
                    content,
                    tool_call_id,
                    json.dumps(tool_calls) if tool_calls else None,
                    name,
                    now,
                ),
            )
            await db.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                (now, self.id),
            )
            await db.commit()
        return mid

    async def get_messages(self) -> list[dict]:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM messages WHERE session_id = ? ORDER BY created_at",
                (self.id,),
            )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def rename(self, name: str):
        now = datetime.utcnow().isoformat()
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE sessions SET name = ?, updated_at = ? WHERE id = ?", (name, now, self.id))
            await db.commit()

    async def delete(self):
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("DELETE FROM messages WHERE session_id = ?", (self.id,))
            await db.execute("DELETE FROM sessions WHERE id = ?", (self.id,))
            await db.commit()
