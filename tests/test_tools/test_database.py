"""Tests for database tool."""
import asyncio
import pytest
import aiosqlite
from pathlib import Path

from tools.database import DatabaseTool


class TestDatabaseTool:
    """Test database tool operations."""

    @pytest.fixture
    def db_tool(self):
        """Create a DatabaseTool instance."""
        return DatabaseTool()

    @pytest.fixture
    def test_db(self, tmp_path):
        """Create a test SQLite database."""
        import sqlite3
        
        db_path = tmp_path / "test.db"
        
        # Create database synchronously
        conn = sqlite3.connect(str(db_path))
        conn.execute("""
            CREATE TABLE users (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                email TEXT,
                age INTEGER
            )
        """)
        conn.execute("INSERT INTO users (name, email, age) VALUES (?, ?, ?)", 
                     ("Alice", "alice@example.com", 30))
        conn.execute("INSERT INTO users (name, email, age) VALUES (?, ?, ?)", 
                     ("Bob", "bob@example.com", 25))
        conn.commit()
        conn.close()
        
        return str(db_path)

    @pytest.mark.asyncio
    async def test_list_tables(self, db_tool, test_db):
        """Test listing tables in database."""
        result = await db_tool.execute(action="tables", sql="", db_path=test_db)
        
        assert "users" in result.output

    @pytest.mark.asyncio
    async def test_query_data(self, db_tool, test_db):
        """Test querying data from database."""
        result = await db_tool.execute(
            action="query",
            sql="SELECT * FROM users",
            db_path=test_db
        )
        
        assert "Alice" in result.output
        assert "Bob" in result.output

    @pytest.mark.asyncio
    async def test_query_with_params(self, db_tool, test_db):
        """Test parameterized query."""
        result = await db_tool.execute(
            action="query",
            sql="SELECT * FROM users WHERE name = ?",
            params=["Alice"],
            db_path=test_db
        )
        
        assert "Alice" in result.output
        assert "Bob" not in result.output

    @pytest.mark.asyncio
    async def test_insert_data(self, db_tool, test_db):
        """Test inserting data into database."""
        result = await db_tool.execute(
            action="insert",
            sql="INSERT INTO users (name, email, age) VALUES (?, ?, ?)",
            params=["Charlie", "charlie@example.com", 35],
            db_path=test_db
        )
        
        assert "successful" in result.output.lower()
        assert "1" in result.output  # 1 row affected

    @pytest.mark.asyncio
    async def test_update_data(self, db_tool, test_db):
        """Test updating data in database."""
        result = await db_tool.execute(
            action="update",
            sql="UPDATE users SET age = ? WHERE name = ?",
            params=[31, "Alice"],
            db_path=test_db
        )
        
        assert "successful" in result.output.lower()

    @pytest.mark.asyncio
    async def test_delete_data(self, db_tool, test_db):
        """Test deleting data from database."""
        result = await db_tool.execute(
            action="delete",
            sql="DELETE FROM users WHERE name = ?",
            params=["Bob"],
            db_path=test_db
        )
        
        assert "successful" in result.output.lower()

    @pytest.mark.asyncio
    async def test_get_schema(self, db_tool, test_db):
        """Test getting table schema."""
        result = await db_tool.execute(
            action="schema",
            sql="users",
            db_path=test_db
        )
        
        assert "id" in result.output
        assert "name" in result.output
        assert "email" in result.output

    @pytest.mark.asyncio
    async def test_nonexistent_table(self, db_tool, test_db):
        """Test querying nonexistent table."""
        result = await db_tool.execute(
            action="query",
            sql="SELECT * FROM nonexistent",
            db_path=test_db
        )
        
        assert "Error" in result.output or "no such table" in result.output.lower()

    @pytest.mark.asyncio
    async def test_invalid_action(self, db_tool, test_db):
        """Test invalid action returns error."""
        result = await db_tool.execute(action="invalid", sql="SELECT 1", db_path=test_db)
        
        assert "Error" in result.output or "unknown action" in result.output.lower()

    def test_database_tool_schema(self, db_tool):
        """Test that database tool has proper schema."""
        schema = db_tool.schema()
        
        assert schema["name"] == "database"
        assert "description" in schema
        assert "parameters" in schema
        assert "action" in schema["parameters"]["properties"]
        assert "sql" in schema["parameters"]["properties"]
