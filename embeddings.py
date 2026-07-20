"""
CodeAssist Embeddings - Generate and search vector embeddings for knowledge base.

Uses OpenAI-compatible embedding APIs for vector representations.
"""

import json
import logging
import struct
from typing import Optional

import openai

from config import Config
from session import get_db
from knowledge import KnowledgeBase

log = logging.getLogger(__name__)

# Default embedding model (can be overridden in config)
DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"

# Embedding dimensions (depends on model)
EMBEDDING_DIMENSIONS = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
}


class EmbeddingClient:
    """Generate embeddings using OpenAI-compatible API."""
    
    def __init__(self, config: Config):
        self.config = config
        self.model = getattr(config.llm, 'embedding_model', None) or DEFAULT_EMBEDDING_MODEL
        
        # Create OpenAI client for embeddings
        kwargs = {"api_key": config.llm.api_key} if config.llm.api_key else {}
        if config.llm.base_url:
            kwargs["base_url"] = config.llm.base_url
        self.client = openai.AsyncOpenAI(**kwargs)
    
    async def embed(self, text: str) -> list[float] | None:
        """Generate embedding for a single text."""
        try:
            response = await self.client.embeddings.create(
                model=self.model,
                input=text[:8000],  # Truncate to avoid token limits
            )
            return response.data[0].embedding
        except Exception as e:
            log.warning("Embedding generation failed: %s", e)
            return None
    
    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for multiple texts."""
        try:
            # Truncate texts
            truncated = [t[:8000] for t in texts]
            
            response = await self.client.embeddings.create(
                model=self.model,
                input=truncated,
            )
            
            # Sort by index to maintain order
            embeddings = [None] * len(texts)
            for item in response.data:
                embeddings[item.index] = item.embedding
            
            return embeddings
        except Exception as e:
            log.warning("Batch embedding generation failed: %s", e)
            return [None] * len(texts)


def serialize_embedding(embedding: list[float]) -> bytes:
    """Serialize embedding to bytes for storage."""
    if not embedding:
        return b""
    return struct.pack(f"{len(embedding)}f", *embedding)


def deserialize_embedding(data: bytes) -> list[float]:
    """Deserialize embedding from bytes."""
    if not data:
        return []
    count = len(data) // 4  # float32 = 4 bytes
    return list(struct.unpack(f"{count}f", data))


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Calculate cosine similarity between two vectors."""
    if not a or not b or len(a) != len(b):
        return 0.0
    
    dot_product = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    
    if norm_a == 0 or norm_b == 0:
        return 0.0
    
    return dot_product / (norm_a * norm_b)


class EmbeddingManager:
    """Manage embeddings for the knowledge base."""
    
    def __init__(self, client: EmbeddingClient | None = None):
        self.client = client
    
    def _get_client(self) -> EmbeddingClient | None:
        """Get or create embedding client."""
        if self.client is None:
            try:
                config = Config.load()
                self.client = EmbeddingClient(config)
            except Exception as e:
                log.warning("Could not create embedding client: %s", e)
                return None
        return self.client
    
    async def generate_and_store_embedding(self, entry_id: str, content: str) -> bool:
        """Generate embedding for a knowledge entry and store it."""
        client = self._get_client()
        if not client:
            return False
        
        try:
            embedding = await client.embed(content)
            if not embedding:
                return False
            
            # Store embedding
            serialized = serialize_embedding(embedding)
            async with get_db() as db:
                await db.execute(
                    "UPDATE knowledge_entries SET embedding = ? WHERE id = ?",
                    (serialized, entry_id),
                )
                await db.commit()
            
            log.debug("Stored embedding for entry %s", entry_id)
            return True
            
        except Exception as e:
            log.warning("Failed to generate/store embedding for %s: %s", entry_id, e)
            return False
    
    async def generate_embeddings_for_all(self, batch_size: int = 10) -> int:
        """Generate embeddings for all knowledge entries without embeddings."""
        client = self._get_client()
        if not client:
            return 0
        
        try:
            async with get_db() as db:
                # Get entries without embeddings
                cursor = await db.execute(
                    """SELECT id, content FROM knowledge_entries 
                       WHERE embedding IS NULL OR embedding = ''
                       LIMIT ?""",
                    (batch_size,),
                )
                entries = await cursor.fetchall()
                
                if not entries:
                    return 0
                
                # Generate embeddings in batch
                texts = [entry["content"][:8000] for entry in entries]
                embeddings = await client.embed_batch(texts)
                
                # Store embeddings
                count = 0
                for entry, embedding in zip(entries, embeddings):
                    if embedding:
                        serialized = serialize_embedding(embedding)
                        await db.execute(
                            "UPDATE knowledge_entries SET embedding = ? WHERE id = ?",
                            (serialized, entry["id"]),
                        )
                        count += 1
                
                await db.commit()
                log.info("Generated embeddings for %d entries", count)
                return count
                
        except Exception as e:
            log.warning("Failed to generate embeddings: %s", e)
            return 0
    
    async def search_by_embedding(
        self,
        query: str,
        limit: int = 10,
        entry_type: str | None = None,
        min_confidence: float = 0.0,
    ) -> list[dict]:
        """Search knowledge entries by embedding similarity."""
        client = self._get_client()
        if not client:
            # Fallback to text search
            return await KnowledgeBase.search_knowledge(
                entry_type=entry_type,
                min_confidence=min_confidence,
                limit=limit,
            )
        
        try:
            # Generate query embedding
            query_embedding = await client.embed(query)
            if not query_embedding:
                # Fallback to text search
                return await KnowledgeBase.search_knowledge(
                    entry_type=entry_type,
                    min_confidence=min_confidence,
                    limit=limit,
                )
            
            # Get all entries with embeddings
            conditions = ["confidence >= ?", "embedding IS NOT NULL", "embedding != ''"]
            params: list = [min_confidence]
            
            if entry_type:
                conditions.append("entry_type = ?")
                params.append(entry_type)
            
            where_clause = " AND ".join(conditions)
            
            async with get_db() as db:
                cursor = await db.execute(
                    f"""SELECT id, entry_type, scope, scope_identifier, content, 
                               confidence, tags, embedding
                        FROM knowledge_entries 
                        WHERE {where_clause}""",
                    params,
                )
                entries = await cursor.fetchall()
            
            # Calculate similarities
            results = []
            for entry in entries:
                entry_embedding = deserialize_embedding(entry["embedding"])
                if entry_embedding:
                    similarity = cosine_similarity(query_embedding, entry_embedding)
                    result = dict(entry)
                    result["similarity"] = similarity
                    del result["embedding"]  # Don't return raw embedding
                    results.append(result)
            
            # Sort by similarity
            results.sort(key=lambda x: x["similarity"], reverse=True)
            
            return results[:limit]
            
        except Exception as e:
            log.warning("Embedding search failed, falling back to text search: %s", e)
            return await KnowledgeBase.search_knowledge(
                entry_type=entry_type,
                min_confidence=min_confidence,
                limit=limit,
            )
    
    async def search_by_entry_embedding(
        self,
        entry_id: str,
        limit: int = 5,
    ) -> list[dict]:
        """Find similar entries based on an existing entry's embedding."""
        try:
            async with get_db() as db:
                # Get the source entry's embedding
                cursor = await db.execute(
                    "SELECT embedding FROM knowledge_entries WHERE id = ?",
                    (entry_id,),
                )
                row = await cursor.fetchone()
                if not row or not row["embedding"]:
                    return []
                
                source_embedding = deserialize_embedding(row["embedding"])
                if not source_embedding:
                    return []
            
            # Get all other entries with embeddings
            async with get_db() as db:
                cursor = await db.execute(
                    """SELECT id, entry_type, scope, scope_identifier, content, 
                              confidence, tags, embedding
                       FROM knowledge_entries 
                       WHERE id != ? AND embedding IS NOT NULL AND embedding != ''""",
                    (entry_id,),
                )
                entries = await cursor.fetchall()
            
            # Calculate similarities
            results = []
            for entry in entries:
                entry_embedding = deserialize_embedding(entry["embedding"])
                if entry_embedding:
                    similarity = cosine_similarity(source_embedding, entry_embedding)
                    result = dict(entry)
                    result["similarity"] = similarity
                    del result["embedding"]
                    results.append(result)
            
            # Sort by similarity
            results.sort(key=lambda x: x["similarity"], reverse=True)
            
            return results[:limit]
            
        except Exception as e:
            log.warning("Failed to find similar entries: %s", e)
            return []


# Singleton instance
_embedding_manager: EmbeddingManager | None = None


def get_embedding_manager() -> EmbeddingManager:
    """Get or create the embedding manager singleton."""
    global _embedding_manager
    if _embedding_manager is None:
        _embedding_manager = EmbeddingManager()
    return _embedding_manager
