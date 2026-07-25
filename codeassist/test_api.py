"""
Test script for CodeAssist Knowledge Base API endpoints.
Requires the server to be running on localhost:8000 (or configured port).
"""

import asyncio
import json
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

try:
    import httpx
except ImportError:
    print("Error: httpx not installed. Run: pip install httpx")
    sys.exit(1)

BASE_URL = "http://localhost:9100"


async def test_health():
    """Test health endpoint."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{BASE_URL}/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        print("✓ Health check")
        return True


async def test_knowledge_endpoints():
    """Test knowledge CRUD endpoints."""
    async with httpx.AsyncClient() as client:
        # Create knowledge entry
        resp = await client.post(f"{BASE_URL}/api/knowledge", json={
            "entry_type": "pattern",
            "scope": "file",
            "content": "Test pattern for API verification",
            "scope_identifier": "test.py",
            "tags": ["test", "api"],
        })
        assert resp.status_code == 200
        entry_id = resp.json()["id"]
        print(f"✓ Create knowledge entry: {entry_id}")
        
        # Get knowledge entry
        resp = await client.get(f"{BASE_URL}/api/knowledge/{entry_id}")
        assert resp.status_code == 200
        assert resp.json()["content"] == "Test pattern for API verification"
        print("✓ Get knowledge entry")
        
        # Update knowledge entry
        resp = await client.put(f"{BASE_URL}/api/knowledge/{entry_id}", json={
            "confidence": 0.95,
            "tags": ["test", "api", "verified"],
        })
        assert resp.status_code == 200
        print("✓ Update knowledge entry")
        
        # List knowledge entries
        resp = await client.get(f"{BASE_URL}/api/knowledge", params={"entry_type": "pattern"})
        assert resp.status_code == 200
        entries = resp.json()
        assert len(entries) >= 1
        print(f"✓ List knowledge entries: {len(entries)} found")
        
        # Search knowledge
        resp = await client.get(f"{BASE_URL}/api/knowledge/search", params={"q": "test pattern"})
        assert resp.status_code == 200
        print("✓ Search knowledge entries")
        
        # Delete knowledge entry
        resp = await client.delete(f"{BASE_URL}/api/knowledge/{entry_id}")
        assert resp.status_code == 200
        print("✓ Delete knowledge entry")


async def test_analytics_endpoints():
    """Test analytics endpoints."""
    async with httpx.AsyncClient() as client:
        # Tool stats
        resp = await client.get(f"{BASE_URL}/api/analytics/tools")
        assert resp.status_code == 200
        print("✓ Get tool stats")
        
        # LLM stats
        resp = await client.get(f"{BASE_URL}/api/analytics/llm")
        assert resp.status_code == 200
        print("✓ Get LLM stats")


async def test_session_tags_endpoints():
    """Test session tags endpoints."""
    async with httpx.AsyncClient() as client:
        # Create a test session first
        resp = await client.post(f"{BASE_URL}/api/sessions")
        assert resp.status_code == 200
        session_id = resp.json()["id"]
        print(f"✓ Create test session: {session_id}")
        
        # Add tag
        resp = await client.post(f"{BASE_URL}/api/sessions/{session_id}/tags", json={
            "tag": "test-tag",
            "source": "test",
        })
        assert resp.status_code == 200
        print("✓ Add session tag")
        
        # Get tags
        resp = await client.get(f"{BASE_URL}/api/sessions/{session_id}/tags")
        assert resp.status_code == 200
        tags = resp.json()
        assert "test-tag" in tags
        print(f"✓ Get session tags: {tags}")
        
        # Search by tags
        resp = await client.get(f"{BASE_URL}/api/sessions/search/tags", params={"tags": "test-tag"})
        assert resp.status_code == 200
        print("✓ Search sessions by tags")
        
        # Cleanup
        resp = await client.delete(f"{BASE_URL}/api/sessions/{session_id}")
        assert resp.status_code == 200
        print("✓ Cleanup test session")


async def test_file_history_endpoint():
    """Test file history endpoint."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{BASE_URL}/api/files/history", params={"file_path": "test.py"})
        assert resp.status_code == 200
        print("✓ Get file history")


async def run_all_tests():
    """Run all API tests."""
    print("=" * 60)
    print("CodeAssist Knowledge Base API Tests")
    print("=" * 60)
    
    try:
        await test_health()
        await test_knowledge_endpoints()
        await test_analytics_endpoints()
        await test_session_tags_endpoints()
        await test_file_history_endpoint()
        
        print("\n" + "=" * 60)
        print("✓ All API tests passed!")
        print("=" * 60)
        return True
        
    except httpx.ConnectError:
        print("\n✗ Could not connect to server at", BASE_URL)
        print("  Make sure the server is running: python server.py")
        return False
    except Exception as e:
        print(f"\n✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = asyncio.run(run_all_tests())
    sys.exit(0 if success else 1)
