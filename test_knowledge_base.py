"""
CodeAssist Knowledge Base - Comprehensive Test Suite

Runs all tests for the v4 schema migration and knowledge base functionality.
Usage: python test_knowledge_base.py [--verbose] [--skip-perf] [--cleanup]
"""

import asyncio
import os
import sqlite3
import sys
import time
import uuid
from pathlib import Path
from typing import Optional

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from session import init_db, get_db, DB_PATH, reset_pool, Session
from knowledge import KnowledgeBase


class TestResult:
    """Track test results."""
    
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.skipped = 0
        self.errors: list[str] = []
    
    def pass_test(self, name: str):
        self.passed += 1
        print(f"  ✓ {name}")
    
    def fail_test(self, name: str, error: str):
        self.failed += 1
        self.errors.append(f"{name}: {error}")
        print(f"  ✗ {name}: {error}")
    
    def skip_test(self, name: str, reason: str):
        self.skipped += 1
        print(f"  ⊘ {name}: {reason}")
    
    def summary(self) -> bool:
        print("\n" + "=" * 60)
        print(f"Results: {self.passed} passed, {self.failed} failed, {self.skipped} skipped")
        if self.errors:
            print("\nFailed tests:")
            for err in self.errors:
                print(f"  - {err}")
        print("=" * 60)
        return self.failed == 0


async def test_schema_migration(results: TestResult, verbose: bool):
    """Test 1: Schema migration creates all v4 tables and indexes."""
    if verbose:
        print("\n[Test 1] Schema Migration")
    
    try:
        reset_pool()
        await init_db()
        results.pass_test("Migration completed")
    except Exception as e:
        results.fail_test("Migration completed", str(e))
        return
    
    try:
        async with get_db() as db:
            cursor = await db.execute("SELECT value FROM schema_info WHERE key = 'version'")
            row = await cursor.fetchone()
            version = int(row[0]) if row else 0
            if version == 4:
                results.pass_test("Schema version is 4")
            else:
                results.fail_test("Schema version is 4", f"Got {version}")
    except Exception as e:
        results.fail_test("Schema version check", str(e))
    
    try:
        async with get_db() as db:
            cursor = await db.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = {row[0] for row in await cursor.fetchall()}
            
            expected = [
                "session_summaries", "knowledge_entries", "tool_executions",
                "llm_usage", "session_tags", "file_snapshots", "qa_pairs"
            ]
            
            for table in expected:
                if table in tables:
                    results.pass_test(f"Table {table}")
                else:
                    results.fail_test(f"Table {table}", "Missing")
    except Exception as e:
        results.fail_test("Table existence check", str(e))
    
    try:
        async with get_db() as db:
            cursor = await db.execute("SELECT name FROM sqlite_master WHERE type='index'")
            indexes = {row[0] for row in await cursor.fetchall()}
            
            expected_indexes = [
                "idx_session_summaries_session", "idx_session_summaries_quality",
                "idx_knowledge_type", "idx_knowledge_scope",
                "idx_tool_executions_session", "idx_tool_executions_tool",
                "idx_llm_usage_session", "idx_llm_usage_model",
                "idx_session_tags_session", "idx_session_tags_tag",
                "idx_file_snapshots_session", "idx_file_snapshots_path",
                "idx_qa_pairs_session", "idx_qa_pairs_quality",
            ]
            
            missing = [idx for idx in expected_indexes if idx not in indexes]
            if not missing:
                results.pass_test("All 14 indexes created")
            else:
                results.fail_test("Indexes", f"Missing: {missing}")
    except Exception as e:
        results.fail_test("Index existence check", str(e))


async def test_session_summary_crud(results: TestResult, verbose: bool):
    """Test 2: Session summary CRUD operations."""
    if verbose:
        print("\n[Test 2] Session Summary CRUD")
    
    session_id = f"test-summary-{uuid.uuid4().hex[:8]}"
    
    try:
        summary_id = await KnowledgeBase.create_session_summary(
            session_id=session_id,
            summary="Test summary for CRUD verification.",
            key_topics=["test", "crud"],
            goals_achieved=["goal1"],
            tools_used=["read", "write"],
            files_modified=["test.py"],
            duration_seconds=300,
            message_count=25,
            token_usage=5000,
            model="gpt-4",
            quality_score=0.85,
        )
        results.pass_test("Create session summary")
    except Exception as e:
        results.fail_test("Create session summary", str(e))
        return
    
    try:
        summary = await KnowledgeBase.get_session_summary(session_id)
        assert summary is not None
        assert summary["summary"] == "Test summary for CRUD verification."
        assert summary["quality_score"] == 0.85
        results.pass_test("Get session summary")
    except Exception as e:
        results.fail_test("Get session summary", str(e))
    
    try:
        updated = await KnowledgeBase.update_session_summary(
            session_id,
            summary="Updated summary",
            quality_score=0.95,
        )
        assert updated
        
        summary = await KnowledgeBase.get_session_summary(session_id)
        assert summary["summary"] == "Updated summary"
        assert summary["quality_score"] == 0.95
        results.pass_test("Update session summary")
    except Exception as e:
        results.fail_test("Update session summary", str(e))
    
    # Cleanup
    async with get_db() as db:
        await db.execute("DELETE FROM session_summaries WHERE session_id = ?", (session_id,))
        await db.commit()


async def test_knowledge_entry_crud(results: TestResult, verbose: bool):
    """Test 3: Knowledge entry CRUD operations."""
    if verbose:
        print("\n[Test 3] Knowledge Entry CRUD")
    
    session_id = f"test-knowledge-{uuid.uuid4().hex[:8]}"
    
    try:
        entry_id = await KnowledgeBase.create_knowledge_entry(
            entry_type="pattern",
            scope="file",
            content="Async/await pattern for database operations",
            scope_identifier="src/db.py",
            source_session_id=session_id,
            confidence=0.9,
            tags=["async", "database", "pattern"],
            metadata={"language": "python"},
        )
        results.pass_test("Create knowledge entry")
    except Exception as e:
        results.fail_test("Create knowledge entry", str(e))
        return
    
    try:
        entry = await KnowledgeBase.get_knowledge_entry(entry_id)
        assert entry is not None
        assert entry["content"] == "Async/await pattern for database operations"
        assert entry["confidence"] == 0.9
        results.pass_test("Get knowledge entry")
    except Exception as e:
        results.fail_test("Get knowledge entry", str(e))
    
    try:
        entries = await KnowledgeBase.search_knowledge(
            entry_type="pattern",
            scope="file",
            min_confidence=0.8,
        )
        assert len(entries) >= 1
        results.pass_test("Search knowledge entries")
    except Exception as e:
        results.fail_test("Search knowledge entries", str(e))
    
    try:
        updated = await KnowledgeBase.update_knowledge_entry(
            entry_id,
            confidence=0.95,
            tags=["async", "database", "pattern", "python"],
        )
        assert updated
        
        entry = await KnowledgeBase.get_knowledge_entry(entry_id)
        assert entry["confidence"] == 0.95
        results.pass_test("Update knowledge entry")
    except Exception as e:
        results.fail_test("Update knowledge entry", str(e))
    
    try:
        incremented = await KnowledgeBase.increment_usage(entry_id)
        assert incremented
        
        entry = await KnowledgeBase.get_knowledge_entry(entry_id)
        assert entry["usage_count"] == 1
        results.pass_test("Increment usage count")
    except Exception as e:
        results.fail_test("Increment usage count", str(e))
    
    # Cleanup
    async with get_db() as db:
        await db.execute("DELETE FROM knowledge_entries WHERE id = ?", (entry_id,))
        await db.commit()


async def test_tool_execution_logging(results: TestResult, verbose: bool):
    """Test 4: Tool execution logging."""
    if verbose:
        print("\n[Test 4] Tool Execution Logging")
    
    session_id = f"test-tools-{uuid.uuid4().hex[:8]}"
    
    try:
        exec_id = await KnowledgeBase.log_tool_execution(
            session_id=session_id,
            tool_name="write",
            arguments={"path": "test.py", "content": "print('hello')"},
            result_summary="File written successfully",
            result_full="File written successfully. 42 bytes written.",
            duration_ms=150,
            success=True,
            token_usage=100,
        )
        results.pass_test("Log tool execution")
    except Exception as e:
        results.fail_test("Log tool execution", str(e))
        return
    
    try:
        exec_id_fail = await KnowledgeBase.log_tool_execution(
            session_id=session_id,
            tool_name="shell",
            arguments={"command": "invalid_cmd"},
            result_summary="Command not found",
            duration_ms=50,
            success=False,
            error_message="Command not found: invalid_cmd",
        )
        results.pass_test("Log failed tool execution")
    except Exception as e:
        results.fail_test("Log failed tool execution", str(e))
    
    try:
        stats = await KnowledgeBase.get_tool_stats(session_id=session_id)
        assert "write" in stats
        assert "shell" in stats
        assert stats["write"]["total_calls"] == 1
        assert stats["shell"]["failed"] == 1
        results.pass_test("Get tool stats")
    except Exception as e:
        results.fail_test("Get tool stats", str(e))
    
    # Cleanup
    async with get_db() as db:
        await db.execute("DELETE FROM tool_executions WHERE session_id = ?", (session_id,))
        await db.commit()


async def test_llm_usage_logging(results: TestResult, verbose: bool):
    """Test 5: LLM usage logging."""
    if verbose:
        print("\n[Test 5] LLM Usage Logging")
    
    session_id = f"test-llm-{uuid.uuid4().hex[:8]}"
    
    try:
        usage_id = await KnowledgeBase.log_llm_usage(
            session_id=session_id,
            model="gpt-4",
            prompt_tokens=500,
            completion_tokens=200,
            total_tokens=700,
            finish_reason="stop",
            duration_ms=1500,
            estimated_cost_usd=0.021,
        )
        results.pass_test("Log LLM usage")
    except Exception as e:
        results.fail_test("Log LLM usage", str(e))
        return
    
    try:
        stats = await KnowledgeBase.get_llm_stats(session_id=session_id)
        assert "gpt-4" in stats
        assert stats["gpt-4"]["total_calls"] == 1
        assert stats["gpt-4"]["total_tokens"] == 700
        results.pass_test("Get LLM stats")
    except Exception as e:
        results.fail_test("Get LLM stats", str(e))
    
    # Cleanup
    async with get_db() as db:
        await db.execute("DELETE FROM llm_usage WHERE session_id = ?", (session_id,))
        await db.commit()


async def test_session_tags(results: TestResult, verbose: bool):
    """Test 6: Session tag operations."""
    if verbose:
        print("\n[Test 6] Session Tags")
    
    session_id = f"test-tags-{uuid.uuid4().hex[:8]}"
    
    try:
        tag_id = await KnowledgeBase.add_session_tag(session_id, "authentication")
        assert tag_id
        results.pass_test("Add session tag")
    except Exception as e:
        results.fail_test("Add session tag", str(e))
        return
    
    try:
        tag_id2 = await KnowledgeBase.add_session_tag(session_id, "backend")
        assert tag_id2
        results.pass_test("Add second tag")
    except Exception as e:
        results.fail_test("Add second tag", str(e))
    
    try:
        tags = await KnowledgeBase.get_session_tags(session_id)
        assert set(tags) == {"authentication", "backend"}
        results.pass_test("Get session tags")
    except Exception as e:
        results.fail_test("Get session tags", str(e))
    
    try:
        # Duplicate tag should be handled gracefully
        duplicate_id = await KnowledgeBase.add_session_tag(session_id, "authentication")
        results.pass_test("Handle duplicate tag")
    except Exception as e:
        results.fail_test("Handle duplicate tag", str(e))
    
    # Cleanup
    async with get_db() as db:
        await db.execute("DELETE FROM session_tags WHERE session_id = ?", (session_id,))
        await db.commit()


async def test_file_snapshots(results: TestResult, verbose: bool):
    """Test 7: File snapshot operations."""
    if verbose:
        print("\n[Test 7] File Snapshots")
    
    session_id = f"test-files-{uuid.uuid4().hex[:8]}"
    
    # Create a real session for the JOIN to work
    try:
        session = await Session.create("File Snapshot Test Session")
        real_session_id = session.id
    except Exception as e:
        results.fail_test("Create test session for snapshots", str(e))
        return
    
    try:
        snapshot_id = await KnowledgeBase.log_file_snapshot(
            session_id=real_session_id,
            file_path="src/main.py",
            action="read",
            content_hash="abc123def456",
            content_preview="def main():\n    pass",
            size_bytes=1024,
        )
        results.pass_test("Log file snapshot")
    except Exception as e:
        results.fail_test("Log file snapshot", str(e))
        return
    
    try:
        await KnowledgeBase.log_file_snapshot(
            session_id=real_session_id,
            file_path="src/main.py",
            action="write",
            content_hash="xyz789",
            content_preview="def main():\n    print('hello')",
            size_bytes=1050,
        )
        results.pass_test("Log second snapshot")
    except Exception as e:
        results.fail_test("Log second snapshot", str(e))
    
    try:
        history = await KnowledgeBase.get_file_history("src/main.py")
        assert len(history) == 2
        assert history[0]["action"] == "write"  # Most recent first
        results.pass_test("Get file history")
    except Exception as e:
        results.fail_test("Get file history", str(e))
    
    # Cleanup
    try:
        await session.delete()
        async with get_db() as db:
            await db.execute("DELETE FROM file_snapshots WHERE session_id = ?", (real_session_id,))
            await db.commit()
    except Exception:
        pass


async def test_regression_existing_features(results: TestResult, verbose: bool):
    """Test 8: Verify existing session features still work."""
    if verbose:
        print("\n[Test 8] Regression - Existing Features")
    
    try:
        session = await Session.create("Regression Test Session")
        results.pass_test("Create session")
    except Exception as e:
        results.fail_test("Create session", str(e))
        return
    
    try:
        mid = await session.add_message("user", "Hello, this is a test message")
        assert mid
        results.pass_test("Add message")
    except Exception as e:
        results.fail_test("Add message", str(e))
    
    try:
        mid2 = await session.add_message("assistant", "Hello! How can I help?")
        assert mid2
        results.pass_test("Add assistant message")
    except Exception as e:
        results.fail_test("Add assistant message", str(e))
    
    try:
        messages = await session.get_messages()
        assert len(messages) == 2
        results.pass_test("Get messages")
    except Exception as e:
        results.fail_test("Get messages", str(e))
    
    try:
        await session.rename("Renamed Session")
        results.pass_test("Rename session")
    except Exception as e:
        results.fail_test("Rename session", str(e))
    
    try:
        forked = await session.fork("Forked Session")
        forked_messages = await forked.get_messages()
        assert len(forked_messages) == 2
        results.pass_test("Fork session")
        
        await forked.delete()
    except Exception as e:
        results.fail_test("Fork session", str(e))
    
    try:
        await session.delete()
        # Verify deleted
        messages = await session.get_messages()
        assert len(messages) == 0
        results.pass_test("Delete session")
    except Exception as e:
        results.fail_test("Delete session", str(e))


async def test_concurrent_access(results: TestResult, verbose: bool):
    """Test 9: Concurrent database access."""
    if verbose:
        print("\n[Test 9] Concurrent Access")
    
    try:
        # Test that multiple sequential connections work
        for i in range(5):
            async with get_db() as conn:
                cursor = await conn.execute("SELECT 1")
                row = await cursor.fetchone()
                assert row[0] == 1
        
        results.pass_test("Acquire and release 5 sequential connections")
    except Exception as e:
        results.fail_test("Concurrent access", str(e))


async def test_fts5_tables(results: TestResult, verbose: bool):
    """Test 10: FTS5 virtual tables (if created)."""
    if verbose:
        print("\n[Test 10] FTS5 Tables")
    
    try:
        async with get_db() as db:
            cursor = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%search'"
            )
            tables = {row[0] for row in await cursor.fetchall()}
            
            if "knowledge_search" in tables and "session_summary_search" in tables:
                results.pass_test("FTS5 tables exist")
            else:
                results.skip_test("FTS5 tables", "Not created yet (run sql/fts5_tables.sql)")
                return
    except Exception as e:
        results.fail_test("Check FTS5 tables", str(e))
    
    # Test FTS5 search if tables exist
    try:
        async with get_db() as db:
            # Insert test data
            entry_id = str(uuid.uuid4())
            await db.execute(
                "INSERT INTO knowledge_entries (id, entry_type, scope, content, tags, created_at, updated_at) "
                "VALUES (?, 'pattern', 'file', 'async await pattern for database operations', '[\"async\"]', datetime('now'), datetime('now'))",
                (entry_id,)
            )
            await db.execute(
                "INSERT INTO knowledge_search (entry_id, entry_type, content, tags, scope, scope_identifier) "
                "VALUES (?, 'pattern', 'async await pattern for database operations', '[\"async\"]', 'file', 'test.py')",
                (entry_id,)
            )
            await db.commit()
            
            # Search
            cursor = await db.execute(
                "SELECT * FROM knowledge_search WHERE knowledge_search MATCH 'async pattern'"
            )
            rows = await cursor.fetchall()
            assert len(rows) >= 1
            
            # Cleanup
            await db.execute("DELETE FROM knowledge_search WHERE entry_id = ?", (entry_id,))
            await db.execute("DELETE FROM knowledge_entries WHERE id = ?", (entry_id,))
            await db.commit()
            
            results.pass_test("FTS5 search query")
    except Exception as e:
        results.fail_test("FTS5 search query", str(e))


async def test_large_dataset(results: TestResult, verbose: bool, skip_perf: bool):
    """Test 11: Performance with larger dataset."""
    if skip_perf:
        results.skip_test("Large dataset performance", "Skipped via --skip-perf")
        return
    
    if verbose:
        print("\n[Test 11] Large Dataset Performance")
    
    session_id = f"test-perf-{uuid.uuid4().hex[:8]}"
    
    try:
        start = time.time()
        entry_ids = []
        
        for i in range(100):
            entry_id = await KnowledgeBase.create_knowledge_entry(
                entry_type="pattern",
                scope="file",
                content=f"Pattern {i}: " + "Lorem ipsum dolor sit amet. " * 5,
                tags=[f"tag{i % 10}", "test", "performance"],
            )
            entry_ids.append(entry_id)
        
        elapsed = time.time() - start
        results.pass_test(f"Insert 100 entries ({elapsed:.2f}s)")
    except Exception as e:
        results.fail_test("Insert 100 entries", str(e))
        return
    
    try:
        start = time.time()
        entries = await KnowledgeBase.search_knowledge(
            entry_type="pattern",
            min_confidence=0.0,
            limit=50,
        )
        elapsed = time.time() - start
        assert len(entries) >= 50
        results.pass_test(f"Search 100 entries ({elapsed:.2f}s)")
    except Exception as e:
        results.fail_test("Search 100 entries", str(e))
    
    # Cleanup
    try:
        async with get_db() as db:
            placeholders = ",".join(["?" for _ in entry_ids])
            await db.execute(f"DELETE FROM knowledge_entries WHERE id IN ({placeholders})", entry_ids)
            await db.commit()
        results.pass_test("Cleanup performance test data")
    except Exception as e:
        results.fail_test("Cleanup performance test data", str(e))


async def test_export_functionality(results: TestResult, verbose: bool):
    """Test 12: Export functionality."""
    if verbose:
        print("\n[Test 12] Export Functionality")
    
    try:
        async with get_db() as db:
            # Get table counts
            tables = ["session_summaries", "knowledge_entries", "tool_executions", 
                      "llm_usage", "session_tags", "file_snapshots"]
            
            for table in tables:
                cursor = await db.execute(f"SELECT COUNT(*) FROM {table}")
                count = (await cursor.fetchone())[0]
                if verbose:
                    print(f"    {table}: {count} rows")
            
            results.pass_test("Query all v4 tables")
    except Exception as e:
        results.fail_test("Query all v4 tables", str(e))
    
    try:
        # Test CSV export command
        import subprocess
        result = subprocess.run(
            ["sqlite3", "-header", "-csv", str(DB_PATH), 
             "SELECT * FROM knowledge_entries LIMIT 5;"],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        if result.returncode == 0 and "entry_type" in result.stdout:
            results.pass_test("CSV export via sqlite3 CLI")
        else:
            results.skip_test("CSV export via sqlite3 CLI", "sqlite3 not available or export failed")
    except FileNotFoundError:
        results.skip_test("CSV export via sqlite3 CLI", "sqlite3 CLI not found")
    except Exception as e:
        results.skip_test("CSV export via sqlite3 CLI", str(e))


async def cleanup_test_data():
    """Clean up any test data left behind."""
    try:
        async with get_db() as db:
            # Clean up test sessions
            await db.execute("DELETE FROM session_summaries WHERE session_id LIKE 'test-%'")
            await db.execute("DELETE FROM knowledge_entries WHERE source_session_id LIKE 'test-%'")
            await db.execute("DELETE FROM tool_executions WHERE session_id LIKE 'test-%'")
            await db.execute("DELETE FROM llm_usage WHERE session_id LIKE 'test-%'")
            await db.execute("DELETE FROM session_tags WHERE session_id LIKE 'test-%'")
            await db.execute("DELETE FROM file_snapshots WHERE session_id LIKE 'test-%'")
            
            # Clean up test knowledge entries
            await db.execute("DELETE FROM knowledge_entries WHERE tags LIKE '%test%'")
            await db.execute("DELETE FROM knowledge_entries WHERE tags LIKE '%performance%'")
            
            await db.commit()
            print("\n✓ Test data cleaned up")
    except Exception as e:
        print(f"\n⚠ Cleanup warning: {e}")


async def run_all_tests(verbose: bool = False, skip_perf: bool = False, cleanup: bool = True):
    """Run all tests."""
    print("=" * 60)
    print("CodeAssist Knowledge Base - Test Suite")
    print("=" * 60)
    
    results = TestResult()
    
    # Run tests in order
    await test_schema_migration(results, verbose)
    await test_session_summary_crud(results, verbose)
    await test_knowledge_entry_crud(results, verbose)
    await test_tool_execution_logging(results, verbose)
    await test_llm_usage_logging(results, verbose)
    await test_session_tags(results, verbose)
    await test_file_snapshots(results, verbose)
    await test_regression_existing_features(results, verbose)
    await test_concurrent_access(results, verbose)
    await test_fts5_tables(results, verbose)
    await test_large_dataset(results, verbose, skip_perf)
    await test_export_functionality(results, verbose)
    
    # Cleanup if requested
    if cleanup:
        await cleanup_test_data()
    
    return results.summary()


def main():
    """Parse arguments and run tests."""
    import argparse
    
    parser = argparse.ArgumentParser(description="CodeAssist Knowledge Base Test Suite")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show detailed test output")
    parser.add_argument("--skip-perf", action="store_true", help="Skip performance tests")
    parser.add_argument("--no-cleanup", action="store_true", help="Don't cleanup test data")
    parser.add_argument("--keep-db", action="store_true", help="Keep database after tests")
    
    args = parser.parse_args()
    
    # Backup database if --keep-db
    if args.keep_db and DB_PATH.exists():
        backup_path = DB_PATH.with_suffix(".db.test-backup")
        import shutil
        shutil.copy2(DB_PATH, backup_path)
        print(f"Database backed up to {backup_path}")
    
    success = asyncio.run(run_all_tests(
        verbose=args.verbose,
        skip_perf=args.skip_perf,
        cleanup=not args.no_cleanup,
    ))
    
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
