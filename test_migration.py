"""
Test script for CodeAssist Knowledge Base migration.
Verifies that v4 schema migration works correctly.
"""

import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from session import init_db, get_db, DB_PATH, reset_pool


async def test_migration():
    """Test the v4 schema migration."""
    print("Testing v4 schema migration...")
    print(f"Database path: {DB_PATH}")
    
    # Reset pool to ensure clean state
    reset_pool()
    
    # Run migration
    print("\n1. Running init_db()...")
    await init_db()
    print("   ✓ Migration completed")
    
    # Verify schema version
    print("\n2. Checking schema version...")
    async with get_db() as db:
        cursor = await db.execute("SELECT value FROM schema_info WHERE key = 'version'")
        row = await cursor.fetchone()
        version = int(row[0]) if row else 0
        print(f"   Schema version: {version}")
        assert version == 4, f"Expected version 4, got {version}"
        print("   ✓ Schema version is 4")
    
    # Verify all v4 tables exist
    print("\n3. Checking v4 tables exist...")
    expected_tables = [
        "session_summaries",
        "knowledge_entries",
        "tool_executions",
        "llm_usage",
        "session_tags",
        "file_snapshots",
        "qa_pairs",
    ]
    
    async with get_db() as db:
        cursor = await db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in await cursor.fetchall()}
        
        for table in expected_tables:
            if table in tables:
                print(f"   ✓ {table}")
            else:
                print(f"   ✗ {table} - MISSING")
                return False
    
    # Verify indexes exist
    print("\n4. Checking indexes...")
    expected_indexes = [
        "idx_session_summaries_session",
        "idx_session_summaries_quality",
        "idx_knowledge_type",
        "idx_knowledge_scope",
        "idx_tool_executions_session",
        "idx_tool_executions_tool",
        "idx_llm_usage_session",
        "idx_llm_usage_model",
        "idx_session_tags_session",
        "idx_session_tags_tag",
        "idx_file_snapshots_session",
        "idx_file_snapshots_path",
        "idx_qa_pairs_session",
        "idx_qa_pairs_quality",
    ]
    
    async with get_db() as db:
        cursor = await db.execute("SELECT name FROM sqlite_master WHERE type='index'")
        indexes = {row[0] for row in await cursor.fetchall()}
        
        for idx in expected_indexes:
            if idx in indexes:
                print(f"   ✓ {idx}")
            else:
                print(f"   ✗ {idx} - MISSING")
    
    # Test KnowledgeBase CRUD operations
    print("\n5. Testing KnowledgeBase operations...")
    
    from knowledge import KnowledgeBase
    
    # Create a test session summary
    summary_id = await KnowledgeBase.create_session_summary(
        session_id="test-session-123",
        summary="Test session for migration verification.",
        key_topics=["testing", "migration"],
        tools_used=["read", "write"],
        quality_score=0.9,
    )
    print(f"   ✓ Created session summary: {summary_id}")
    
    # Get the summary
    summary = await KnowledgeBase.get_session_summary("test-session-123")
    assert summary is not None, "Failed to retrieve session summary"
    assert summary["summary"] == "Test session for migration verification."
    print("   ✓ Retrieved session summary")
    
    # Create a knowledge entry
    entry_id = await KnowledgeBase.create_knowledge_entry(
        entry_type="pattern",
        scope="file",
        content="Test pattern for migration verification.",
        scope_identifier="src/test.py",
        source_session_id="test-session-123",
        tags=["test", "pattern"],
    )
    print(f"   ✓ Created knowledge entry: {entry_id}")
    
    # Get the entry
    entry = await KnowledgeBase.get_knowledge_entry(entry_id)
    assert entry is not None, "Failed to retrieve knowledge entry"
    assert entry["content"] == "Test pattern for migration verification."
    print("   ✓ Retrieved knowledge entry")
    
    # Log a tool execution
    execution_id = await KnowledgeBase.log_tool_execution(
        session_id="test-session-123",
        tool_name="read",
        arguments={"path": "src/test.py"},
        result_summary="File read successfully",
        duration_ms=150,
        success=True,
    )
    print(f"   ✓ Logged tool execution: {execution_id}")
    
    # Log LLM usage
    usage_id = await KnowledgeBase.log_llm_usage(
        session_id="test-session-123",
        model="gpt-4",
        prompt_tokens=100,
        completion_tokens=50,
        total_tokens=150,
        duration_ms=1200,
        estimated_cost_usd=0.015,
    )
    print(f"   ✓ Logged LLM usage: {usage_id}")
    
    # Add session tag
    tag_id = await KnowledgeBase.add_session_tag(
        session_id="test-session-123",
        tag="testing",
        source="auto",
    )
    print(f"   ✓ Added session tag: {tag_id}")
    
    # Log file snapshot
    snapshot_id = await KnowledgeBase.log_file_snapshot(
        session_id="test-session-123",
        file_path="src/test.py",
        action="read",
        content_hash="abc123",
        content_preview="def test(): pass",
        size_bytes=100,
    )
    print(f"   ✓ Logged file snapshot: {snapshot_id}")
    
    # Get tool stats
    stats = await KnowledgeBase.get_tool_stats(session_id="test-session-123")
    assert "read" in stats, "Tool stats missing 'read'"
    print("   ✓ Retrieved tool stats")
    
    # Get LLM stats
    llm_stats = await KnowledgeBase.get_llm_stats(session_id="test-session-123")
    assert "gpt-4" in llm_stats, "LLM stats missing 'gpt-4'"
    print("   ✓ Retrieved LLM stats")
    
    # Search sessions by tags
    sessions = await KnowledgeBase.search_sessions_by_tags(["testing"])
    print(f"   ✓ Found {len(sessions)} session(s) with tag 'testing'")
    
    # Get file history
    history = await KnowledgeBase.get_file_history("src/test.py")
    print(f"   ✓ Retrieved file history: {len(history)} entry(ies)")
    
    # Clean up test data
    print("\n6. Cleaning up test data...")
    async with get_db() as db:
        await db.execute("DELETE FROM session_summaries WHERE session_id = 'test-session-123'")
        await db.execute("DELETE FROM knowledge_entries WHERE source_session_id = 'test-session-123'")
        await db.execute("DELETE FROM tool_executions WHERE session_id = 'test-session-123'")
        await db.execute("DELETE FROM llm_usage WHERE session_id = 'test-session-123'")
        await db.execute("DELETE FROM session_tags WHERE session_id = 'test-session-123'")
        await db.execute("DELETE FROM file_snapshots WHERE session_id = 'test-session-123'")
        await db.commit()
    print("   ✓ Test data cleaned up")
    
    print("\n" + "="*60)
    print("✓ All migration tests passed!")
    print("="*60)
    
    return True


if __name__ == "__main__":
    success = asyncio.run(test_migration())
    sys.exit(0 if success else 1)
