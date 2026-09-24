"""Tests for embeddings (serialize/deserialize/cosine) and semantic-search routing."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import codeassist.embeddings as embeddings_mod
from codeassist.knowledge import KnowledgeBase
from codeassist.routes.knowledge import router as knowledge_router
from codeassist.session import init_db


class TestSerializeDeserialize:
    def test_roundtrip_empty(self):
        assert embeddings_mod.deserialize_embedding(embeddings_mod.serialize_embedding([])) == []

    def test_roundtrip_basic(self):
        vec = [0.1, 0.2, 0.3, -1.0, 3.5]
        blob = embeddings_mod.serialize_embedding(vec)
        assert isinstance(blob, bytes)
        restored = embeddings_mod.deserialize_embedding(blob)
        assert restored == pytest.approx(vec, abs=1e-6)

    def test_empty_blob_returns_empty_list(self):
        assert embeddings_mod.deserialize_embedding(b"") == []


class TestCosineSimilarity:
    def test_identical_vectors_are_one(self):
        v = [1.0, 2.0, 3.0]
        assert embeddings_mod.cosine_similarity(v, v) == pytest.approx(1.0)

    def test_orthogonal_vectors_are_zero(self):
        assert embeddings_mod.cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    def test_mismatched_lengths_are_zero(self):
        assert embeddings_mod.cosine_similarity([1.0, 2.0], [1.0]) == 0.0

    def test_empty_is_zero(self):
        assert embeddings_mod.cosine_similarity([], [1.0]) == 0.0


class TestSemanticSearchRouting:
    @pytest.fixture(autouse=True)
    def _init(self):
        # Ensure a clean slate for knowledge entries between routing tests.
        pass

    @pytest.mark.asyncio
    async def test_semantic_available_returns_semantic_type(self, monkeypatch):
        await init_db()
        manager = MagicMock()
        manager._get_client.return_value = object()
        manager.search_by_embedding = AsyncMock(return_value=[{"id": "1", "similarity": 0.9}])
        monkeypatch.setattr(embeddings_mod, "get_embedding_manager", lambda: manager)

        from fastapi import FastAPI

        from codeassist.routes.kb_gui import router

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        resp = client.get("/api/kb/search", params={"q": "hello", "semantic": True})
        assert resp.status_code == 200
        body = resp.json()
        assert body["type"] == "semantic"
        assert body["results"][0]["similarity"] == 0.9

    @pytest.mark.asyncio
    async def test_semantic_no_client_falls_back_to_text(self, monkeypatch):
        await init_db()
        entry_id = await KnowledgeBase.create_knowledge_entry(
            entry_type="pattern", scope="file", content="alpha beta gamma"
        )
        manager = MagicMock()
        manager._get_client.return_value = None
        monkeypatch.setattr(embeddings_mod, "get_embedding_manager", lambda: manager)

        from fastapi import FastAPI

        from codeassist.routes.kb_gui import router

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        resp = client.get("/api/kb/search", params={"q": "alpha", "semantic": True})
        assert resp.status_code == 200
        body = resp.json()
        # No embedding client -> must not claim semantic results (F4).
        assert body["type"] == "text"
        assert any(r["id"] == entry_id for r in body["results"])

    @pytest.mark.asyncio
    async def test_semantic_fallback_without_similarity_reports_text(self, monkeypatch):
        await init_db()
        manager = MagicMock()
        manager._get_client.return_value = object()
        # Client exists but search fell back internally (no similarity field).
        manager.search_by_embedding = AsyncMock(return_value=[{"id": "1"}])
        monkeypatch.setattr(embeddings_mod, "get_embedding_manager", lambda: manager)

        from fastapi import FastAPI

        from codeassist.routes.kb_gui import router

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        resp = client.get("/api/kb/search", params={"q": "alpha", "semantic": True})
        assert resp.status_code == 200
        assert resp.json()["type"] == "text"

    @pytest.mark.asyncio
    async def test_non_semantic_returns_text(self, monkeypatch):
        await init_db()
        manager = MagicMock()
        # A client should never be consulted for a plain text search.
        monkeypatch.setattr(embeddings_mod, "get_embedding_manager", lambda: manager)

        from fastapi import FastAPI

        from codeassist.routes.kb_gui import router

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        resp = client.get("/api/kb/search", params={"q": "alpha"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["type"] == "text"
        manager._get_client.assert_not_called()


class TestGenerateEmbeddingsEndpoint:
    def _app(self):
        app = FastAPI()
        app.include_router(knowledge_router)
        return TestClient(app)

    @pytest.mark.asyncio
    async def test_generate_endpoint_returns_count(self):
        await init_db()
        client = self._app()
        resp = client.post("/api/knowledge/embeddings/generate")
        assert resp.status_code == 200
        # No embedding model configured in tests -> nothing to generate.
        assert resp.json() == {"generated": 0}


class TestEntryStatusRoute:
    def _app(self):
        from codeassist.routes.kb_gui import router
        app = FastAPI()
        app.include_router(router)
        return TestClient(app)

    @pytest.mark.asyncio
    async def test_set_status_roundtrip(self):
        await init_db()
        entry_id = await KnowledgeBase.create_knowledge_entry(
            entry_type="pattern", scope="file", content="route status"
        )
        client = self._app()

        resp = client.post(f"/api/kb/entries/{entry_id}/status",
                           json={"status": "review"})
        assert resp.status_code == 200
        entry = await KnowledgeBase.get_knowledge_entry(entry_id)
        assert entry["status"] == "review"

    @pytest.mark.asyncio
    async def test_set_status_requires_status(self):
        await init_db()
        entry_id = await KnowledgeBase.create_knowledge_entry(
            entry_type="pattern", scope="file", content="no status"
        )
        client = self._app()
        resp = client.post(f"/api/kb/entries/{entry_id}/status", json={})
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_set_status_missing_entry(self):
        await init_db()
        client = self._app()
        resp = client.post("/api/kb/entries/nope/status", json={"status": "active"})
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_viewing_entry_increments_usage(self):
        await init_db()
        entry_id = await KnowledgeBase.create_knowledge_entry(
            entry_type="pattern", scope="file", content="usage tracking"
        )
        client = self._app()

        resp = client.get(f"/api/kb/entries/{entry_id}")
        assert resp.status_code == 200
        entry = await KnowledgeBase.get_knowledge_entry(entry_id)
        assert entry["usage_count"] == 1

        # A second view bumps it again.
        client.get(f"/api/kb/entries/{entry_id}")
        entry = await KnowledgeBase.get_knowledge_entry(entry_id)
        assert entry["usage_count"] == 2
