import aiosqlite
import asyncio
import json
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import dataclass

# The database lives in a data directory that can be overridden via
# CODEASSIST_DATA_DIR (used by Docker to point at the mounted persistence
# volume). Defaults to <package>/data for plain local runs.
DB_PATH = Path(os.environ.get("CODEASSIST_DATA_DIR", Path(__file__).parent / "data")) / "codeassist.db"

SCHEMA_VERSION = 7


class _DBPool:
    """Simple async connection pool for aiosqlite."""

    def __init__(self, db_path: Path, max_connections: int = 5):
        self._db_path = db_path
        self._max_connections = max_connections
        self._pool: asyncio.Queue[aiosqlite.Connection] = asyncio.Queue(maxsize=max_connections)
        self._count = 0

    async def acquire(self) -> aiosqlite.Connection:
        try:
            conn = self._pool.get_nowait()
        except asyncio.QueueEmpty:
            if self._count < self._max_connections:
                self._count += 1
                conn = await aiosqlite.connect(self._db_path)
                _ALL_CONNS.add(conn)
                conn.row_factory = aiosqlite.Row
                # Enable WAL mode and set busy timeout for concurrent access
                await conn.execute("PRAGMA journal_mode=WAL")
                await conn.execute("PRAGMA busy_timeout=5000")
            else:
                conn = await self._pool.get()
        return conn

    async def release(self, conn: aiosqlite.Connection):
        try:
            self._pool.put_nowait(conn)
        except asyncio.QueueFull:
            await conn.close()
            _ALL_CONNS.discard(conn)
            self._count -= 1


_db_pool: _DBPool | None = None

# Every aiosqlite connection the pool has ever opened. aiosqlite runs one
# non-daemon worker thread per connection; those threads only exit when the
# connection receives aiosqlite's STOP sentinel (via conn.close()/stop()). Keep
# them tracked so pool resets and interpreter shutdown can always stop them.
_ALL_CONNS: set[aiosqlite.Connection] = set()


def _get_pool() -> _DBPool:
    global _db_pool
    if _db_pool is None:
        _db_pool = _DBPool(DB_PATH)
    return _db_pool


def _stop_all_conns():
    """Stop every known aiosqlite worker thread (sync, loop-independent)."""
    for conn in list(_ALL_CONNS):
        try:
            conn.stop()
        except Exception:
            pass
    _ALL_CONNS.clear()


def reset_pool():
    """Drop all pooled connections. Call before deleting the DB file in tests."""
    global _db_pool
    _stop_all_conns()
    if _db_pool is not None:
        while not _db_pool._pool.empty():
            try:
                _db_pool._pool.get_nowait()
            except asyncio.QueueEmpty:
                break
        _db_pool._count = 0
        _db_pool = None


async def async_reset_pool():
    """Properly close all pooled connections asynchronously."""
    global _db_pool
    for conn in list(_ALL_CONNS):
        try:
            await conn.close()
        except Exception:
            pass
    _ALL_CONNS.clear()
    if _db_pool is not None:
        while not _db_pool._pool.empty():
            try:
                _db_pool._pool.get_nowait()
            except asyncio.QueueEmpty:
                break
        _db_pool._count = 0
        _db_pool = None


@asynccontextmanager
async def get_db():
    """Get a pooled database connection."""
    pool = _get_pool()
    conn = await pool.acquire()
    try:
        yield conn
    finally:
        await pool.release(conn)


async def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with get_db() as db:
        await db.execute(f"""
            CREATE TABLE IF NOT EXISTS schema_info (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)

        current_version = 0
        try:
            cursor = await db.execute("SELECT value FROM schema_info WHERE key = 'version'")
            row = await cursor.fetchone()
            if row:
                current_version = int(row[0])
        except Exception:
            pass

        if current_version == 0:
            await _create_v1_tables(db)
            current_version = 1

        if current_version < 2:
            await _add_v2_tables(db)
            current_version = 2

        if current_version < 3:
            await _add_v3_tables(db)
            current_version = 3

        if current_version < 4:
            await _add_v4_tables(db)
            current_version = 4

        if current_version < 5:
            await _add_v5_tables(db)
            current_version = 5

        if current_version < 6:
            await _add_v6_tables(db)
            current_version = 6

        if current_version < 7:
            await _add_v7_tables(db)
            current_version = 7

        await db.execute(
            "INSERT OR REPLACE INTO schema_info (key, value) VALUES ('version', ?)",
            (str(current_version),)
        )
        await db.commit()

    # Create FTS5 virtual tables after commit (cannot be inside transactions)
    if current_version >= 4:
        await _ensure_fts5_tables()


async def _create_v1_tables(db):
    await db.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            name TEXT,
            parent_id TEXT,
            fork_point TEXT,
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


async def _add_v2_tables(db):
    await db.execute("""
        CREATE TABLE IF NOT EXISTS agents (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            description TEXT,
            instructions TEXT,
            model TEXT,
            max_iterations INTEGER,
            permissions TEXT,
            enabled INTEGER DEFAULT 1,
            created_at TEXT,
            updated_at TEXT
        )
    """)

    await db.execute("""
        CREATE TABLE IF NOT EXISTS mcp_servers (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            config TEXT,
            enabled INTEGER DEFAULT 1,
            created_at TEXT,
            updated_at TEXT
        )
    """)

    await db.execute("""
        CREATE TABLE IF NOT EXISTS skills (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT,
            content TEXT,
            source TEXT,
            slash_command TEXT,
            enabled INTEGER DEFAULT 1,
            created_at TEXT,
            updated_at TEXT
        )
    """)

    await db.execute("""
        CREATE TABLE IF NOT EXISTS plugins (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            version TEXT,
            config TEXT,
            enabled INTEGER DEFAULT 1,
            created_at TEXT,
            updated_at TEXT
        )
    """)

    await db.execute("""
        CREATE TABLE IF NOT EXISTS lsp_servers (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            command TEXT,
            args TEXT,
            languages TEXT,
            enabled INTEGER DEFAULT 1,
            created_at TEXT,
            updated_at TEXT
        )
    """)

    await db.execute("""
        CREATE TABLE IF NOT EXISTS git_repos (
            id TEXT PRIMARY KEY,
            path TEXT NOT NULL UNIQUE,
            head_branch TEXT,
            last_sync TEXT,
            created_at TEXT,
            updated_at TEXT
        )
    """)


async def _add_v3_tables(db):
    await db.execute("""
        CREATE TABLE IF NOT EXISTS permissions (
            id TEXT PRIMARY KEY,
            agent_id TEXT REFERENCES agents(id),
            tool_name TEXT NOT NULL,
            action TEXT NOT NULL,
            scope TEXT,
            created_at TEXT,
            UNIQUE(agent_id, tool_name, action)
        )
    """)

    await db.execute("""
        CREATE TABLE IF NOT EXISTS session_exports (
            id TEXT PRIMARY KEY,
            session_id TEXT REFERENCES sessions(id),
            data TEXT,
            redacted INTEGER DEFAULT 0,
            created_at TEXT,
            expires_at TEXT
        )
    """)


async def _add_v4_tables(db):
    """Add knowledge base tables for Phase 1."""
    
    # 1. Session summaries
    await db.execute("""
        CREATE TABLE IF NOT EXISTS session_summaries (
            id TEXT PRIMARY KEY,
            session_id TEXT REFERENCES sessions(id) ON DELETE CASCADE,
            summary TEXT NOT NULL,
            key_topics TEXT,
            goals_achieved TEXT,
            tools_used TEXT,
            files_modified TEXT,
            duration_seconds INTEGER,
            message_count INTEGER,
            token_usage INTEGER,
            model TEXT,
            quality_score REAL,
            created_at TEXT,
            updated_at TEXT,
            UNIQUE(session_id)
        )
    """)
    
    # 2. Knowledge entries
    await db.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_entries (
            id TEXT PRIMARY KEY,
            entry_type TEXT NOT NULL,
            scope TEXT NOT NULL,
            scope_identifier TEXT,
            content TEXT NOT NULL,
            source_session_id TEXT,
            confidence REAL DEFAULT 1.0,
            usage_count INTEGER DEFAULT 0,
            tags TEXT,
            metadata TEXT,
            embedding TEXT,
            created_at TEXT,
            updated_at TEXT
        )
    """)
    
    # 3. Tool executions
    await db.execute("""
        CREATE TABLE IF NOT EXISTS tool_executions (
            id TEXT PRIMARY KEY,
            session_id TEXT REFERENCES sessions(id) ON DELETE CASCADE,
            tool_name TEXT NOT NULL,
            arguments TEXT,
            result_summary TEXT,
            result_full TEXT,
            duration_ms INTEGER,
            success INTEGER DEFAULT 1,
            error_message TEXT,
            token_usage INTEGER,
            created_at TEXT
        )
    """)
    
    # 4. LLM usage
    await db.execute("""
        CREATE TABLE IF NOT EXISTS llm_usage (
            id TEXT PRIMARY KEY,
            session_id TEXT REFERENCES sessions(id) ON DELETE CASCADE,
            model TEXT NOT NULL,
            prompt_tokens INTEGER,
            completion_tokens INTEGER,
            total_tokens INTEGER,
            finish_reason TEXT,
            duration_ms INTEGER,
            estimated_cost_usd REAL,
            created_at TEXT
        )
    """)
    
    # 5. Session tags
    await db.execute("""
        CREATE TABLE IF NOT EXISTS session_tags (
            id TEXT PRIMARY KEY,
            session_id TEXT REFERENCES sessions(id) ON DELETE CASCADE,
            tag TEXT NOT NULL,
            source TEXT DEFAULT 'user',
            created_at TEXT,
            UNIQUE(session_id, tag)
        )
    """)
    
    # 6. File snapshots
    await db.execute("""
        CREATE TABLE IF NOT EXISTS file_snapshots (
            id TEXT PRIMARY KEY,
            session_id TEXT REFERENCES sessions(id) ON DELETE CASCADE,
            file_path TEXT NOT NULL,
            action TEXT NOT NULL,
            content_hash TEXT,
            content_preview TEXT,
            size_bytes INTEGER,
            created_at TEXT
        )
    """)
    
    # 7. Q&A pairs
    await db.execute("""
        CREATE TABLE IF NOT EXISTS qa_pairs (
            id TEXT PRIMARY KEY,
            session_id TEXT REFERENCES sessions(id) ON DELETE CASCADE,
            question TEXT NOT NULL,
            answer_summary TEXT,
            context TEXT,
            tools_used TEXT,
            success INTEGER DEFAULT 1,
            quality_score REAL,
            quality_notes TEXT,
            tags TEXT,
            metadata TEXT,
            created_at TEXT
        )
    """)
    
    # 8. Create indexes
    indexes = [
        "CREATE INDEX IF NOT EXISTS idx_session_summaries_session ON session_summaries(session_id)",
        "CREATE INDEX IF NOT EXISTS idx_session_summaries_quality ON session_summaries(quality_score)",
        "CREATE INDEX IF NOT EXISTS idx_knowledge_type ON knowledge_entries(entry_type)",
        "CREATE INDEX IF NOT EXISTS idx_knowledge_scope ON knowledge_entries(scope, scope_identifier)",
        "CREATE INDEX IF NOT EXISTS idx_knowledge_source ON knowledge_entries(source_session_id)",
        "CREATE INDEX IF NOT EXISTS idx_knowledge_confidence ON knowledge_entries(confidence)",
        "CREATE INDEX IF NOT EXISTS idx_tool_executions_session ON tool_executions(session_id)",
        "CREATE INDEX IF NOT EXISTS idx_tool_executions_tool ON tool_executions(tool_name)",
        "CREATE INDEX IF NOT EXISTS idx_tool_executions_success ON tool_executions(success)",
        "CREATE INDEX IF NOT EXISTS idx_tool_executions_created ON tool_executions(created_at)",
        "CREATE INDEX IF NOT EXISTS idx_llm_usage_session ON llm_usage(session_id)",
        "CREATE INDEX IF NOT EXISTS idx_llm_usage_model ON llm_usage(model)",
        "CREATE INDEX IF NOT EXISTS idx_llm_usage_created ON llm_usage(created_at)",
        "CREATE INDEX IF NOT EXISTS idx_session_tags_session ON session_tags(session_id)",
        "CREATE INDEX IF NOT EXISTS idx_session_tags_tag ON session_tags(tag)",
        "CREATE INDEX IF NOT EXISTS idx_file_snapshots_session ON file_snapshots(session_id)",
        "CREATE INDEX IF NOT EXISTS idx_file_snapshots_path ON file_snapshots(file_path)",
        "CREATE INDEX IF NOT EXISTS idx_file_snapshots_action ON file_snapshots(action)",
        "CREATE INDEX IF NOT EXISTS idx_qa_pairs_session ON qa_pairs(session_id)",
        "CREATE INDEX IF NOT EXISTS idx_qa_pairs_quality ON qa_pairs(quality_score)",
        "CREATE INDEX IF NOT EXISTS idx_qa_pairs_success ON qa_pairs(success)",
    ]
    
    for idx in indexes:
        await db.execute(idx)
    
    await db.commit()


async def _add_v5_tables(db):
    """Add tables for Phase A features: questions, snapshots, todos, permission_saves."""

    # Question persistence for structured questions
    await db.execute("""
        CREATE TABLE IF NOT EXISTS questions (
            id TEXT PRIMARY KEY,
            session_id TEXT REFERENCES sessions(id) ON DELETE CASCADE,
            message_id TEXT,
            tool_call_id TEXT,
            questions TEXT,
            answers TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT
        )
    """)

    # Snapshot tracking for revert system
    await db.execute("""
        CREATE TABLE IF NOT EXISTS snapshots (
            id TEXT PRIMARY KEY,
            session_id TEXT REFERENCES sessions(id) ON DELETE CASCADE,
            turn_number INTEGER,
            git_hash TEXT,
            created_at TEXT
        )
    """)

    # Todo persistence for subagent isolation
    await db.execute("""
        CREATE TABLE IF NOT EXISTS todos (
            id TEXT PRIMARY KEY,
            session_id TEXT REFERENCES sessions(id) ON DELETE CASCADE,
            content TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            priority TEXT NOT NULL DEFAULT 'medium',
            position INTEGER,
            created_at TEXT
        )
    """)

    # Saved permission preferences
    await db.execute("""
        CREATE TABLE IF NOT EXISTS permission_saves (
            id TEXT PRIMARY KEY,
            tool_name TEXT NOT NULL,
            pattern TEXT NOT NULL,
            action TEXT NOT NULL,
            created_at TEXT
        )
    """)

    await db.commit()


async def _add_v6_tables(db):
    """Add message attachments for multimodal (image) chat attachments."""

    await db.execute("""
        CREATE TABLE IF NOT EXISTS message_attachments (
            id TEXT PRIMARY KEY,
            message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
            attachment_type TEXT NOT NULL DEFAULT 'image',
            mime_type TEXT NOT NULL,
            file_name TEXT,
            data TEXT NOT NULL,
            created_at TEXT
        )
    """)

    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_message_attachments_message ON message_attachments(message_id)"
    )
    await db.commit()


async def _add_v7_tables(db):
    """Add per-session agent selection (agent switcher persistence)."""

    # SQLite lacks ADD COLUMN IF NOT EXISTS; guard against duplicate column.
    cursor = await db.execute("PRAGMA table_info(sessions)")
    rows = await cursor.fetchall()
    if not any(r["name"] == "agent_name" for r in rows):
        await db.execute("ALTER TABLE sessions ADD COLUMN agent_name TEXT")
        await db.commit()


async def _ensure_fts5_tables():
    """Create FTS5 virtual tables if they don't exist."""
    try:
        async with get_db() as db:
            # Check if knowledge_search exists
            cursor = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='knowledge_search'"
            )
            if not await cursor.fetchone():
                await db.execute("""
                    CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_search USING fts5(
                        entry_id UNINDEXED,
                        entry_type,
                        content,
                        tags,
                        scope,
                        scope_identifier
                    )
                """)
                
                # Add triggers for incremental updates
                await db.execute("""
                    CREATE TRIGGER IF NOT EXISTS knowledge_search_ai AFTER INSERT ON knowledge_entries BEGIN
                        INSERT INTO knowledge_search(entry_id, entry_type, content, tags, scope, scope_identifier)
                        VALUES (new.id, new.entry_type, new.content, new.tags, new.scope, new.scope_identifier);
                    END
                """)
                await db.execute("""
                    CREATE TRIGGER IF NOT EXISTS knowledge_search_ad AFTER DELETE ON knowledge_entries BEGIN
                        DELETE FROM knowledge_search WHERE entry_id = old.id;
                    END
                """)
                await db.execute("""
                    CREATE TRIGGER IF NOT EXISTS knowledge_search_au AFTER UPDATE ON knowledge_entries BEGIN
                        DELETE FROM knowledge_search WHERE entry_id = old.id;
                        INSERT INTO knowledge_search(entry_id, entry_type, content, tags, scope, scope_identifier)
                        VALUES (new.id, new.entry_type, new.content, new.tags, new.scope, new.scope_identifier);
                    END
                """)
            
            # Check if session_summary_search exists
            cursor = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='session_summary_search'"
            )
            if not await cursor.fetchone():
                await db.execute("""
                    CREATE VIRTUAL TABLE IF NOT EXISTS session_summary_search USING fts5(
                        summary_id UNINDEXED,
                        session_id UNINDEXED,
                        summary,
                        key_topics,
                        tools_used,
                        files_modified
                    )
                """)
                
                # Add triggers for incremental updates
                await db.execute("""
                    CREATE TRIGGER IF NOT EXISTS session_summary_search_ai AFTER INSERT ON session_summaries BEGIN
                        INSERT INTO session_summary_search(summary_id, session_id, summary, key_topics, tools_used, files_modified)
                        VALUES (new.id, new.session_id, new.summary, new.key_topics, new.tools_used, new.files_modified);
                    END
                """)
                await db.execute("""
                    CREATE TRIGGER IF NOT EXISTS session_summary_search_ad AFTER DELETE ON session_summaries BEGIN
                        DELETE FROM session_summary_search WHERE summary_id = old.id;
                    END
                """)
                await db.execute("""
                    CREATE TRIGGER IF NOT EXISTS session_summary_search_au AFTER UPDATE ON session_summaries BEGIN
                        DELETE FROM session_summary_search WHERE summary_id = old.id;
                        INSERT INTO session_summary_search(summary_id, session_id, summary, key_topics, tools_used, files_modified)
                        VALUES (new.id, new.session_id, new.summary, new.key_topics, new.tools_used, new.files_modified);
                    END
                """)
            
            await db.commit()
    except Exception as e:
        # FTS5 creation can fail if extension not available - log but don't crash
        import logging
        logging.getLogger(__name__).warning("Could not create FTS5 tables: %s", e)


class Session:
    def __init__(self, session_id: str):
        self.id = session_id

    @classmethod
    async def create(cls, name: str | None = None, parent_id: str | None = None, fork_point: str | None = None) -> "Session":
        sid = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        now_str = now.isoformat()
        default_name = name or now.strftime("%Y-%m-%d %H:%M")
        async with get_db() as db:
            await db.execute(
                "INSERT INTO sessions (id, name, parent_id, fork_point, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (sid, default_name, parent_id, fork_point, now_str, now_str),
            )
            await db.commit()
        return cls(sid)

    @classmethod
    async def list_all(cls) -> list[dict]:
        async with get_db() as db:
            cursor = await db.execute("SELECT * FROM sessions ORDER BY updated_at DESC")
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    @classmethod
    async def get_or_create_latest(cls) -> "Session":
        async with get_db() as db:
            cursor = await db.execute("SELECT id FROM sessions ORDER BY updated_at DESC LIMIT 1")
            row = await cursor.fetchone()
            if row:
                return cls(row[0])
        return await cls.create()

    @classmethod
    async def get_or_create(cls, session_id: str) -> "Session":
        """Return the session with the given id, creating it if absent.

        Atomic via INSERT OR IGNORE so concurrent connections with the same id
        can't race; prevents orphaned message rows referencing an unknown session."""
        now = datetime.now(timezone.utc)
        now_str = now.isoformat()
        async with get_db() as db:
            await db.execute(
                "INSERT OR IGNORE INTO sessions (id, name, parent_id, fork_point, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (session_id, now.strftime("%Y-%m-%d %H:%M"), None, None, now_str, now_str),
            )
            await db.commit()
        return cls(session_id)

    async def get_agent_name(self) -> str | None:
        """Agent selected for this session (persisted), if any."""
        async with get_db() as db:
            cursor = await db.execute(
                "SELECT agent_name FROM sessions WHERE id = ?", (self.id,)
            )
            row = await cursor.fetchone()
            if row and row[0]:
                return row[0]
        return None

    async def set_agent_name(self, agent_name: str):
        """Persist the agent selected for this session."""
        now = datetime.now(timezone.utc).isoformat()
        async with get_db() as db:
            await db.execute(
                "UPDATE sessions SET agent_name = ?, updated_at = ? WHERE id = ?",
                (agent_name, now, self.id),
            )
            await db.commit()

    async def add_message(
        self,
        role: str,
        content: str | None = None,
        tool_calls: list[dict] | None = None,
        tool_call_id: str | None = None,
        name: str | None = None,
        attachments: list[dict] | None = None,
    ) -> str:
        mid = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        async with get_db() as db:
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
            for att in attachments or []:
                await db.execute(
                    "INSERT INTO message_attachments (id, message_id, attachment_type, mime_type, file_name, data, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(uuid.uuid4()),
                        mid,
                        att.get("attachment_type", "image"),
                        att.get("mime_type", "application/octet-stream"),
                        att.get("file_name"),
                        att.get("data", ""),
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
        async with get_db() as db:
            cursor = await db.execute(
                "SELECT * FROM messages WHERE session_id = ? ORDER BY created_at",
                (self.id,),
            )
            rows = await cursor.fetchall()
            messages = [dict(r) for r in rows]

            attachment_rows = []
            if messages:
                cursor = await db.execute(
                    "SELECT ma.* FROM message_attachments ma "
                    "JOIN messages m ON ma.message_id = m.id "
                    "WHERE m.session_id = ? ORDER BY ma.created_at",
                    (self.id,),
                )
                attachment_rows = await cursor.fetchall()

            by_message: dict[str, list[dict]] = {}
            for att in attachment_rows:
                entry = {
                    "attachment_type": att["attachment_type"],
                    "mime_type": att["mime_type"],
                    "file_name": att["file_name"],
                    "data": att["data"],
                }
                by_message.setdefault(att["message_id"], []).append(entry)

            for msg in messages:
                msg["attachments"] = by_message.get(msg["id"], [])
            return messages

    async def update_message(self, message_id: str, content: str | None = None, tool_calls: list[dict] | None = None):
        """Update an existing message's content and/or tool_calls."""
        now = datetime.now(timezone.utc).isoformat()
        async with get_db() as db:
            if content is not None and tool_calls is not None:
                await db.execute(
                    "UPDATE messages SET content = ?, tool_calls = ?, created_at = ? WHERE id = ? AND session_id = ?",
                    (content, json.dumps(tool_calls), now, message_id, self.id),
                )
            elif content is not None:
                await db.execute(
                    "UPDATE messages SET content = ?, created_at = ? WHERE id = ? AND session_id = ?",
                    (content, now, message_id, self.id),
                )
            elif tool_calls is not None:
                await db.execute(
                    "UPDATE messages SET tool_calls = ?, created_at = ? WHERE id = ? AND session_id = ?",
                    (json.dumps(tool_calls), now, message_id, self.id),
                )
            await db.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                (now, self.id),
            )
            await db.commit()

    async def rename(self, name: str):
        now = datetime.now(timezone.utc).isoformat()
        async with get_db() as db:
            await db.execute("UPDATE sessions SET name = ?, updated_at = ? WHERE id = ?", (name, now, self.id))
            await db.commit()

    async def delete(self):
        async with get_db() as db:
            await db.execute(
                "DELETE FROM message_attachments WHERE message_id IN "
                "(SELECT id FROM messages WHERE session_id = ?)",
                (self.id,),
            )
            await db.execute("DELETE FROM messages WHERE session_id = ?", (self.id,))
            await db.execute("DELETE FROM sessions WHERE id = ?", (self.id,))
            await db.commit()

    async def delete_messages_after(self, message_id: str) -> int:
        """Delete all messages after a given message ID. Returns count of deleted messages."""
        async with get_db() as db:
            # Get timestamp of the reference message
            cursor = await db.execute(
                "SELECT created_at FROM messages WHERE id = ? AND session_id = ?",
                (message_id, self.id),
            )
            row = await cursor.fetchone()
            if not row:
                return 0
            ref_ts = row["created_at"]
            await db.execute(
                "DELETE FROM message_attachments WHERE message_id IN "
                "(SELECT id FROM messages WHERE session_id = ? AND created_at > ?)",
                (self.id, ref_ts),
            )
            cursor = await db.execute(
                "DELETE FROM messages WHERE session_id = ? AND created_at > ?",
                (self.id, ref_ts),
            )
            deleted = cursor.rowcount
            now = datetime.now(timezone.utc).isoformat()
            await db.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                (now, self.id),
            )
            await db.commit()
            return deleted

    async def undo_last_turn(self) -> int:
        """Remove the last assistant message and associated tool messages.
        Returns the number of deleted messages, or 0 if there's nothing to undo."""
        msgs = await self.get_messages()
        # Find the last assistant message
        last_assistant_idx = None
        for i in range(len(msgs) - 1, -1, -1):
            if msgs[i]["role"] == "assistant":
                last_assistant_idx = i
                break
        if last_assistant_idx is None:
            return 0
        # Delete from that point onward (assistant + any subsequent tool messages)
        return await self.delete_messages_after(msgs[last_assistant_idx]["id"])

    async def fork(self, name: str | None = None) -> "Session":
        """Create a fork of this session."""
        new_session = await Session.create(name=name, parent_id=self.id, fork_point=datetime.now(timezone.utc).isoformat())
        messages = await self.get_messages()
        for msg in messages:
            await new_session.add_message(
                role=msg["role"],
                content=msg["content"],
                tool_calls=json.loads(msg["tool_calls"]) if msg["tool_calls"] else None,
                tool_call_id=msg["tool_call_id"],
                name=msg["name"],
                attachments=msg.get("attachments"),
            )
        return new_session


class Agent:
    def __init__(self, agent_id: str):
        self.id = agent_id

    @classmethod
    async def create(
        cls,
        name: str,
        description: str | None = None,
        instructions: str | None = None,
        model: str | None = None,
        max_iterations: int | None = None,
        permissions: dict[str, list[str]] | None = None,
    ) -> "Agent":
        aid = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        async with get_db() as db:
            await db.execute(
                "INSERT INTO agents (id, name, description, instructions, model, max_iterations, permissions, enabled, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
                (aid, name, description, instructions, model, max_iterations, json.dumps(permissions or {}), now, now),
            )
            await db.commit()
        return cls(aid)

    @classmethod
    async def list_all(cls) -> list[dict]:
        async with get_db() as db:
            cursor = await db.execute("SELECT * FROM agents WHERE enabled = 1 ORDER BY name")
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    @classmethod
    async def get(cls, agent_id: str) -> "Agent | None":
        async with get_db() as db:
            cursor = await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))
            row = await cursor.fetchone()
            if row:
                return cls(row[0])
        return None

    @classmethod
    async def get_by_name(cls, name: str) -> "Agent | None":
        async with get_db() as db:
            cursor = await db.execute("SELECT * FROM agents WHERE name = ? AND enabled = 1", (name,))
            row = await cursor.fetchone()
            if row:
                return cls(row[0])
        return None

    async def get_config(self) -> dict:
        async with get_db() as db:
            cursor = await db.execute("SELECT * FROM agents WHERE id = ?", (self.id,))
            row = await cursor.fetchone()
            if row:
                return dict(row)
        return {}

    _ALLOWED_UPDATE_FIELDS = {"name", "description", "instructions", "model", "max_iterations", "permissions"}

    async def update(self, **kwargs):
        now = datetime.now(timezone.utc).isoformat()
        fields = []
        values = []
        for key, value in kwargs.items():
            if key not in self._ALLOWED_UPDATE_FIELDS:
                continue
            if key == "permissions":
                fields.append("permissions = ?")
                values.append(json.dumps(value))
            else:
                fields.append(f"{key} = ?")
                values.append(value)
        if not fields:
            return
        fields.append("updated_at = ?")
        values.append(now)
        values.append(self.id)
        async with get_db() as db:
            await db.execute(f"UPDATE agents SET {', '.join(fields)} WHERE id = ?", values)
            await db.commit()

    async def delete(self):
        async with get_db() as db:
            await db.execute("DELETE FROM permissions WHERE agent_id = ?", (self.id,))
            await db.execute("UPDATE agents SET enabled = 0 WHERE id = ?", (self.id,))
            await db.commit()


class MCPServer:
    def __init__(self, server_id: str):
        self.id = server_id

    @classmethod
    async def create(cls, name: str, config: dict) -> "MCPServer":
        sid = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        async with get_db() as db:
            await db.execute(
                "INSERT INTO mcp_servers (id, name, config, enabled, created_at, updated_at) VALUES (?, ?, ?, 1, ?, ?)",
                (sid, name, json.dumps(config), now, now),
            )
            await db.commit()
        return cls(sid)

    @classmethod
    async def list_all(cls) -> list[dict]:
        async with get_db() as db:
            cursor = await db.execute("SELECT * FROM mcp_servers WHERE enabled = 1 ORDER BY name")
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    @classmethod
    async def get(cls, server_id: str) -> "MCPServer | None":
        async with get_db() as db:
            cursor = await db.execute("SELECT * FROM mcp_servers WHERE id = ?", (server_id,))
            row = await cursor.fetchone()
            if row:
                return cls(row[0])
        return None

    async def get_config(self) -> dict:
        async with get_db() as db:
            cursor = await db.execute("SELECT * FROM mcp_servers WHERE id = ?", (self.id,))
            row = await cursor.fetchone()
            if row:
                config = json.loads(row["config"])
                config["id"] = row["id"]
                config["name"] = row["name"]
                return config
        return {}

    async def update(self, config: dict):
        now = datetime.now(timezone.utc).isoformat()
        async with get_db() as db:
            await db.execute(
                "UPDATE mcp_servers SET config = ?, updated_at = ? WHERE id = ?",
                (json.dumps(config), now, self.id),
            )
            await db.commit()

    async def delete(self):
        async with get_db() as db:
            await db.execute("DELETE FROM mcp_servers WHERE id = ?", (self.id,))
            await db.commit()


class Skill:
    def __init__(self, skill_id: str):
        self.id = skill_id

    @classmethod
    async def create(
        cls,
        name: str,
        description: str,
        content: str,
        source: str | None = None,
        slash_command: str | None = None,
    ) -> "Skill":
        sid = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        async with get_db() as db:
            await db.execute(
                "INSERT INTO skills (id, name, description, content, source, slash_command, enabled, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)",
                (sid, name, description, content, source, slash_command, now, now),
            )
            await db.commit()
        return cls(sid)

    @classmethod
    async def list_all(cls) -> list[dict]:
        async with get_db() as db:
            cursor = await db.execute("SELECT * FROM skills WHERE enabled = 1 ORDER BY name")
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    @classmethod
    async def get(cls, skill_id: str) -> "Skill | None":
        async with get_db() as db:
            cursor = await db.execute("SELECT * FROM skills WHERE id = ?", (skill_id,))
            row = await cursor.fetchone()
            if row:
                return cls(row[0])
        return None

    @classmethod
    async def get_by_slash_command(cls, command: str) -> "Skill | None":
        async with get_db() as db:
            cursor = await db.execute("SELECT * FROM skills WHERE slash_command = ? AND enabled = 1", (command,))
            row = await cursor.fetchone()
            if row:
                return cls(row[0])
        return None

    async def delete(self):
        async with get_db() as db:
            await db.execute("UPDATE skills SET enabled = 0 WHERE id = ?", (self.id,))
            await db.commit()


class Plugin:
    def __init__(self, plugin_id: str):
        self.id = plugin_id

    @classmethod
    async def create(cls, name: str, version: str | None = None, config: dict | None = None) -> "Plugin":
        pid = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        async with get_db() as db:
            await db.execute(
                "INSERT INTO plugins (id, name, version, config, enabled, created_at, updated_at) VALUES (?, ?, ?, ?, 1, ?, ?)",
                (pid, name, version, json.dumps(config or {}), now, now),
            )
            await db.commit()
        return cls(pid)

    @classmethod
    async def list_all(cls) -> list[dict]:
        async with get_db() as db:
            cursor = await db.execute("SELECT * FROM plugins WHERE enabled = 1 ORDER BY name")
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    @classmethod
    async def get(cls, plugin_id: str) -> "Plugin | None":
        async with get_db() as db:
            cursor = await db.execute("SELECT * FROM plugins WHERE id = ?", (plugin_id,))
            row = await cursor.fetchone()
            if row:
                return cls(row[0])
        return None

    async def delete(self):
        async with get_db() as db:
            await db.execute("DELETE FROM plugins WHERE id = ?", (self.id,))
            await db.commit()


class LSPServer:
    def __init__(self, server_id: str):
        self.id = server_id

    @classmethod
    async def create(
        cls,
        name: str,
        command: str,
        args: list[str] | None = None,
        languages: list[str] | None = None,
    ) -> "LSPServer":
        sid = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        async with get_db() as db:
            await db.execute(
                "INSERT INTO lsp_servers (id, name, command, args, languages, enabled, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
                (sid, name, command, json.dumps(args or []), json.dumps(languages or []), now, now),
            )
            await db.commit()
        return cls(sid)

    @classmethod
    async def list_all(cls) -> list[dict]:
        async with get_db() as db:
            cursor = await db.execute("SELECT * FROM lsp_servers WHERE enabled = 1 ORDER BY name")
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    @classmethod
    async def get(cls, server_id: str) -> "LSPServer | None":
        async with get_db() as db:
            cursor = await db.execute("SELECT * FROM lsp_servers WHERE id = ?", (server_id,))
            row = await cursor.fetchone()
            if row:
                return cls(row[0])
        return None

    async def get_config(self) -> dict:
        async with get_db() as db:
            cursor = await db.execute("SELECT * FROM lsp_servers WHERE id = ?", (self.id,))
            row = await cursor.fetchone()
            if row:
                return {
                    "id": row["id"],
                    "name": row["name"],
                    "command": row["command"],
                    "args": json.loads(row["args"]),
                    "languages": json.loads(row["languages"]),
                }
        return {}

    async def delete(self):
        async with get_db() as db:
            await db.execute("DELETE FROM lsp_servers WHERE id = ?", (self.id,))
            await db.commit()


class GitRepo:
    def __init__(self, repo_id: str):
        self.id = repo_id

    @classmethod
    async def create(cls, path: str, head_branch: str | None = None) -> "GitRepo":
        rid = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        async with get_db() as db:
            await db.execute(
                "INSERT INTO git_repos (id, path, head_branch, last_sync, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (rid, path, head_branch, now, now, now),
            )
            await db.commit()
        return cls(rid)

    @classmethod
    async def list_all(cls) -> list[dict]:
        async with get_db() as db:
            cursor = await db.execute("SELECT * FROM git_repos ORDER BY path")
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    @classmethod
    async def get_by_path(cls, path: str) -> "GitRepo | None":
        async with get_db() as db:
            cursor = await db.execute("SELECT * FROM git_repos WHERE path = ?", (path,))
            row = await cursor.fetchone()
            if row:
                return cls(row[0])
        return None

    async def update(self, head_branch: str | None = None):
        now = datetime.now(timezone.utc).isoformat()
        async with get_db() as db:
            if head_branch is not None:
                await db.execute(
                    "UPDATE git_repos SET head_branch = ?, last_sync = ?, updated_at = ? WHERE id = ?",
                    (head_branch, now, now, self.id),
                )
            else:
                await db.execute("UPDATE git_repos SET updated_at = ? WHERE id = ?", (now, self.id))
            await db.commit()

    async def delete(self):
        async with get_db() as db:
            await db.execute("DELETE FROM git_repos WHERE id = ?", (self.id,))
            await db.commit()
