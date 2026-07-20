# CodeAssist Knowledge Base Schema Design

**Phase 1: Core Knowledge Base**  
**Schema Version:** 4  
**Status:** Design Document

---

## Executive Summary

This document outlines the database schema extensions to transform CodeAssist from a session-based tool into a persistent knowledge base. The design maintains human-readable storage, enables efficient search, and lays groundwork for future fine-tuning capabilities.

---

## Design Principles

1. **Human-Readable**: All data stored as plain text or JSON in SQLite; accessible via standard SQL tools
2. **Auditable**: External tools (DB Browser, sqlite3 CLI, Python scripts) can query any table
3. **Extensible**: JSON metadata fields allow schema evolution without migrations
4. **Searchable**: FTS5 full-text search for immediate value; vector search placeholder for future
5. **Fine-Tuning Ready**: Structured capture of high-quality Q&A pairs with quality scoring

---

## Current Schema (v3)

Existing tables in `data/codeassist.db`:
- `sessions` - Session metadata (id, name, parent_id, fork_point, timestamps)
- `messages` - Conversation messages (session_id, role, content, tool_calls)
- `agents` - Agent configurations
- `skills` - Skill definitions
- `plugins` - Plugin registry
- `mcp_servers` - MCP server configs
- `lsp_servers` - LSP server configs
- `git_repos` - Git repository tracking
- `permissions` - Per-agent tool permissions
- `session_exports` - Session export cache

---

## Proposed Schema (v4)

### 1. Session Summaries Table

**Purpose**: AI-generated summaries of each session for quick overview and search.

```sql
CREATE TABLE session_summaries (
    id TEXT PRIMARY KEY,
    session_id TEXT REFERENCES sessions(id) ON DELETE CASCADE,
    summary TEXT NOT NULL,           -- Human-readable summary (1-3 paragraphs)
    key_topics TEXT,                 -- JSON array of extracted topics/tags
    goals_achieved TEXT,             -- JSON array of goals/milestones reached
    tools_used TEXT,                 -- JSON array of tools utilized
    files_modified TEXT,             -- JSON array of file paths touched
    duration_seconds INTEGER,        -- Total session duration
    message_count INTEGER,           -- Total messages
    token_usage INTEGER,             -- Estimated total tokens used
    model TEXT,                      -- Primary LLM model used
    quality_score REAL,              -- 0.0-1.0 score for fine-tuning relevance
    created_at TEXT,
    updated_at TEXT,
    UNIQUE(session_id)
);

CREATE INDEX idx_session_summaries_session ON session_summaries(session_id);
CREATE INDEX idx_session_summaries_quality ON session_summaries(quality_score);
```

**Rationale**:
- Pre-computed summary avoids re-reading entire session
- `quality_score` enables filtering for fine-tuning data extraction
- `key_topics` enables tag-based search without full-text
- `files_modified` enables project-level knowledge aggregation

---

### 2. Knowledge Entries Table

**Purpose**: Accumulated project knowledge, patterns, and context across sessions.

```sql
CREATE TABLE knowledge_entries (
    id TEXT PRIMARY KEY,
    entry_type TEXT NOT NULL,        -- 'pattern', 'convention', 'summary', 'snippet', 'decision'
    scope TEXT NOT NULL,             -- 'project', 'file', 'function', 'module', 'global'
    scope_identifier TEXT,           -- e.g., file path, function name, module name
    content TEXT NOT NULL,           -- The knowledge content (human-readable)
    source_session_id TEXT,          -- Where this knowledge was learned
    confidence REAL DEFAULT 1.0,    -- 0.0-1.0 confidence in accuracy
    usage_count INTEGER DEFAULT 0,  -- How often referenced
    tags TEXT,                       -- JSON array of tags for categorization
    metadata TEXT,                   -- JSON object for extensible data
    embedding TEXT,                  -- Placeholder for future vector embedding
    created_at TEXT,
    updated_at TEXT
);

CREATE INDEX idx_knowledge_type ON knowledge_entries(entry_type);
CREATE INDEX idx_knowledge_scope ON knowledge_entries(scope, scope_identifier);
CREATE INDEX idx_knowledge_source ON knowledge_entries(source_session_id);
CREATE INDEX idx_knowledge_confidence ON knowledge_entries(confidence);
```

**Entry Types**:
- `pattern`: Recurring code patterns observed
- `convention`: Project conventions (naming, structure, etc.)
- `summary`: File/module summaries
- `snippet`: Useful code snippets or templates
- `decision`: Architectural or design decisions

**Scopes**:
- `global`: Applies to entire workspace
- `project`: Specific project within workspace
- `file`: Specific file
- `function`: Specific function/class
- `module`: Specific module/package

**Rationale**:
- Accumulates knowledge across sessions without context overhead
- `quality_score` + `usage_count` enables ranking
- `embedding` field ready for future vector search
- JSON `metadata` allows extension without schema changes

---

### 3. Knowledge Search Index (FTS5)

**Purpose**: Full-text search across knowledge entries and session summaries.

```sql
CREATE VIRTUAL TABLE knowledge_search USING fts5(
    entry_id,
    entry_type,
    content,
    tags,
    scope,
    scope_identifier,
    content_rowid='rowid',
    content=knowledge_entries
);

CREATE VIRTUAL TABLE session_summary_search USING fts5(
    summary_id,
    session_id,
    summary,
    key_topics,
    tools_used,
    files_modified,
    content_rowid='rowid',
    content=session_summaries
);
```

**Rationale**:
- FTS5 provides efficient full-text search with ranking
- Human-readable queries (SQL-based)
- Accessible via `sqlite3` CLI and standard tools
- Supports prefix matching, phrase search, boolean operators

---

### 4. Tool Execution Log

**Purpose**: Complete audit trail of tool usage for analytics and debugging.

```sql
CREATE TABLE tool_executions (
    id TEXT PRIMARY KEY,
    session_id TEXT REFERENCES sessions(id) ON DELETE CASCADE,
    tool_name TEXT NOT NULL,
    arguments TEXT,                  -- JSON object of tool arguments
    result_summary TEXT,             -- Truncated result (first 1000 chars)
    result_full TEXT,                -- Complete result (optional, for debugging)
    duration_ms INTEGER,            -- Execution time in milliseconds
    success INTEGER DEFAULT 1,      -- 1=success, 0=error
    error_message TEXT,             -- Error details if failed
    token_usage INTEGER,            -- Tokens consumed by this execution
    created_at TEXT
);

CREATE INDEX idx_tool_executions_session ON tool_executions(session_id);
CREATE INDEX idx_tool_executions_tool ON tool_executions(tool_name);
CREATE INDEX idx_tool_executions_success ON tool_executions(success);
CREATE INDEX idx_tool_executions_created ON tool_executions(created_at);
```

**Rationale**:
- Captures what tools are actually used (vs. available)
- Enables analytics: "most used tools", "failure rates", "average duration"
- `result_full` optional to avoid storage bloat
- Supports debugging: "what did tool X do in session Y?"

---

### 5. LLM Usage Tracking

**Purpose**: Track token consumption and model usage for cost analysis.

```sql
CREATE TABLE llm_usage (
    id TEXT PRIMARY KEY,
    session_id TEXT REFERENCES sessions(id) ON DELETE CASCADE,
    model TEXT NOT NULL,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    total_tokens INTEGER,
    finish_reason TEXT,              -- 'stop', 'length', 'tool_calls', etc.
    duration_ms INTEGER,            -- LLM response time
    estimated_cost_usd REAL,        -- Calculated cost (if model pricing known)
    created_at TEXT
);

CREATE INDEX idx_llm_usage_session ON llm_usage(session_id);
CREATE INDEX idx_llm_usage_model ON llm_usage(model);
CREATE INDEX idx_llm_usage_created ON llm_usage(created_at);
```

**Rationale**:
- Cost tracking per session, model, time period
- Performance analysis: "which model is fastest?", "average tokens per session"
- Enables budget alerts and usage quotas

---

### 6. Session Tags

**Purpose**: Categorize sessions for filtering and search.

```sql
CREATE TABLE session_tags (
    id TEXT PRIMARY KEY,
    session_id TEXT REFERENCES sessions(id) ON DELETE CASCADE,
    tag TEXT NOT NULL,
    source TEXT DEFAULT 'user',      -- 'user', 'auto', 'system'
    created_at TEXT,
    UNIQUE(session_id, tag)
);

CREATE INDEX idx_session_tags_session ON session_tags(session_id);
CREATE INDEX idx_session_tags_tag ON session_tags(tag);
```

**Tag Sources**:
- `user`: Manually applied by user
- `auto`: Auto-generated (e.g., from session summary)
- `system`: Applied by system (e.g., project name, date)

---

### 7. File Snapshots (Phase 1 - Basic)

**Purpose**: Track file state at key points during sessions.

```sql
CREATE TABLE file_snapshots (
    id TEXT PRIMARY KEY,
    session_id TEXT REFERENCES sessions(id) ON DELETE CASCADE,
    file_path TEXT NOT NULL,
    action TEXT NOT NULL,            -- 'read', 'write', 'edit', 'create', 'delete'
    content_hash TEXT,               -- SHA-256 hash for deduplication
    content_preview TEXT,            -- First 500 chars (for quick preview)
    size_bytes INTEGER,
    created_at TEXT
);

CREATE INDEX idx_file_snapshots_session ON file_snapshots(session_id);
CREATE INDEX idx_file_snapshots_path ON file_snapshots(file_path);
CREATE INDEX idx_file_snapshots_action ON file_snapshots(action);
```

**Rationale**:
- Track which files were modified in which sessions
- `content_hash` enables deduplication and change detection
- `content_preview` provides quick lookup without loading full content
- Future: Full snapshots for rollback/restore capabilities

---

### 8. Q&A Pairs (Phase 1 - Capture Structure)

**Purpose**: Capture high-quality Q&A for future fine-tuning.

```sql
CREATE TABLE qa_pairs (
    id TEXT PRIMARY KEY,
    session_id TEXT REFERENCES sessions(id) ON DELETE CASCADE,
    question TEXT NOT NULL,          -- User's request/question
    answer_summary TEXT,             -- Concise answer summary
    context TEXT,                    -- Relevant context (code snippets, file paths)
    tools_used TEXT,                 -- JSON array of tools utilized
    success INTEGER DEFAULT 1,      -- Did the answer solve the problem?
    quality_score REAL,             -- 0.0-1.0 quality rating
    quality_notes TEXT,             -- Why this score was assigned
    tags TEXT,                       -- JSON array for categorization
    metadata TEXT,                   -- JSON for extensible data
    created_at TEXT
);

CREATE INDEX idx_qa_pairs_session ON qa_pairs(session_id);
CREATE INDEX idx_qa_pairs_quality ON qa_pairs(quality_score);
CREATE INDEX idx_qa_pairs_success ON qa_pairs(success);
```

**Rationale**:
- Pre-structured for fine-tuning dataset generation
- `quality_score` + `success` enable filtering for high-value examples
- `context` captures surrounding code/docs for richer training data
- Ready for future automated extraction pipeline

---

## Migration Plan

### Pre-Migration Checklist

1. **Backup existing database**
   ```bash
   cp data/codeassist.db data/codeassist.db.backup.v3
   ```

2. **Verify current schema version**
   ```sql
   SELECT value FROM schema_info WHERE key = 'version';
   -- Should return: 3
   ```

### Migration Script (v3 → v4)

```python
# session.py - _add_v4_tables()

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
    
    # 9. Create FTS5 tables (if not exists)
    # Note: FTS5 tables are created separately via SQL due to virtual table syntax
    # See: knowledge_search_fts.sql
    
    await db.commit()
```

### Post-Migration: FTS5 Virtual Tables

**Automatic (recommended):** FTS5 tables are created automatically on server startup if they don't exist. No manual steps required.

**Manual (if needed):** You can also create them manually:
```bash
sqlite3 data/codeassist.db < sql/fts5_tables.sql
```

**Required for:**
- `fulltext_search_knowledge()` - Search knowledge entries
- `fulltext_search_sessions()` - Search session summaries

**Optional if:** You only use SQL `LIKE` queries for searching (slower but works without FTS5).

-- Knowledge search index
CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_search USING fts5(
    entry_id UNINDEXED,
    entry_type,
    content,
    tags,
    scope,
    scope_identifier
);

-- Session summary search index
CREATE VIRTUAL TABLE IF NOT EXISTS session_summary_search USING fts5(
    summary_id UNINDEXED,
    session_id UNINDEXED,
    summary,
    key_topics,
    tools_used,
    files_modified
);

-- Populate from existing data
INSERT INTO knowledge_search(entry_id, entry_type, content, tags, scope, scope_identifier)
SELECT id, entry_type, content, tags, scope, scope_identifier
FROM knowledge_entries;

INSERT INTO session_summary_search(summary_id, session_id, summary, key_topics, tools_used, files_modified)
SELECT id, session_id, summary, key_topics, tools_used, files_modified
FROM session_summaries;
```

---

## Implementation Integration Points

### 1. Session Completion Hook

**Location**: `session.py` or `server.py` (WebSocket disconnect handler)

**Trigger**: When session ends (WebSocket close, timeout, or explicit completion)

**Actions**:
1. Generate session summary via LLM call
2. Extract key topics, tools used, files modified
3. Calculate `quality_score` based on:
   - Session duration
   - Number of successful tool executions
   - User satisfaction signals (if available)
4. Insert into `session_summaries`
5. Auto-tag session based on summary content

### 2. Tool Execution Logging

**Location**: `tools/__init__.py` (Tool base class) or `agent.py` (Agent loop)

**Trigger**: Every `tool.execute()` call

**Actions**:
1. Capture start time
2. Execute tool
3. Capture end time, success/failure, result
4. Insert into `tool_executions`
5. Log to `llm_usage` if tool triggers LLM call

### 3. Knowledge Extraction Pipeline

**Location**: New module `knowledge.py`

**Trigger**: After session summary generated

**Actions**:
1. Analyze session for reusable knowledge
2. Extract patterns, conventions, decisions
3. Deduplicate against existing knowledge entries
4. Insert new entries with appropriate confidence scores
5. Update FTS5 indexes

### 4. Q&A Extraction Pipeline

**Location**: New module `qa_extractor.py`

**Trigger**: After session summary generated (or manual trigger)

**Actions**:
1. Identify high-quality Q&A exchanges from session
2. Extract question, answer, context
3. Score quality based on:
   - Tool execution success
   - Answer completeness
   - User follow-up (did they ask more questions?)
4. Insert into `qa_pairs` with quality metadata

### 5. Search API Endpoints

**Location**: `server.py`

**New Endpoints**:

```python
# Full-text search across knowledge
GET /api/knowledge/search?q=<query>&type=<entry_type>&scope=<scope>&limit=<n>

# Get knowledge entries
GET /api/knowledge?scope=<scope>&type=<type>&limit=<n>

# Create/update knowledge entry
POST /api/knowledge
{
    "entry_type": "pattern",
    "scope": "file",
    "scope_identifier": "src/main.py",
    "content": "Uses async/await pattern for all I/O operations",
    "tags": ["async", "pattern", "convention"]
}

# Search sessions by tags
GET /api/sessions/tags?tags=<tag1,tag2>&limit=<n>

# Get session summary
GET /api/sessions/{id}/summary

# Get tool execution stats
GET /api/analytics/tools?session_id=<id>&period=<7d|30d|all>

# Get LLM usage stats
GET /api/analytics/llm?session_id=<id>&model=<model>&period=<7d|30d|all>

# Export knowledge base
GET /api/knowledge/export?format=<json|csv>
```

---

## Human-Readable Access Examples

### SQLite CLI Queries

```sql
-- List all session summaries with quality scores
sqlite3 data/codeassist.db \
  "SELECT s.name, k.summary, k.quality_score 
   FROM session_summaries k 
   JOIN sessions s ON k.session_id = s.id 
   ORDER BY k.quality_score DESC 
   LIMIT 10;"

-- Search knowledge for "async patterns"
sqlite3 data/codeassist.db \
  "SELECT entry_type, scope, content 
   FROM knowledge_entries 
   WHERE content LIKE '%async%' 
   ORDER BY confidence DESC;"

-- FTS5 search for "database connection pooling"
sqlite3 data/codeassist.db \
  "SELECT entry_type, scope, content 
   FROM knowledge_search 
   WHERE knowledge_search MATCH 'database connection pooling' 
   ORDER BY rank;"

-- Tool usage statistics
sqlite3 data/codeassist.db \
  "SELECT tool_name, COUNT(*) as uses, 
          AVG(duration_ms) as avg_duration,
          SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) as failures
   FROM tool_executions 
   GROUP BY tool_name 
   ORDER BY uses DESC;"

-- Export session summary to CSV
sqlite3 -header -csv data/codeassist.db \
  "SELECT * FROM session_summaries;" > summaries.csv
```

### DB Browser for SQLite

1. Open `data/codeassist.db`
2. Browse tables visually
3. Run custom queries
4. Export to CSV/JSON
5. View table relationships

### Python Script Access

```python
import sqlite3
import json

conn = sqlite3.connect('data/codeassist.db')
conn.row_factory = sqlite3.Row

# Get all knowledge entries for a specific file
cursor = conn.execute(
    "SELECT * FROM knowledge_entries WHERE scope = ? AND scope_identifier = ?",
    ('file', 'src/main.py')
)

for row in cursor:
    print(f"Type: {row['entry_type']}")
    print(f"Content: {row['content']}")
    print(f"Confidence: {row['confidence']}")
    print("---")

conn.close()
```

---

## Future Considerations (Phase 2+)

### Vector Search (Semantic Similarity)

**Option A: sqlite-vec extension**
```sql
-- Future: Add vector column and index
ALTER TABLE knowledge_entries ADD COLUMN embedding BLOB;

-- Load sqlite-vec extension
-- CREATE VIRTUAL TABLE knowledge_vec USING vec0(...);
```

**Option B: Separate vector store**
- Use ChromaDB or FAISS for embeddings
- Sync with SQLite for metadata
- More complex but better performance at scale

### Fine-Tuning Dataset Generation

```python
# Future: qa_export.py
def export_fine_tuning_dataset(
    min_quality: float = 0.8,
    min_success: int = 1,
    tags: list[str] = None,
    format: str = 'jsonl'  # or 'alpaca', 'sharegpt'
) -> list[dict]:
    """Export high-quality Q&A pairs for fine-tuning."""
    
    query = """
        SELECT question, answer_summary, context, tools_used, quality_score
        FROM qa_pairs
        WHERE quality_score >= ? AND success = ?
    """
    params = [min_quality, min_success]
    
    if tags:
        query += " AND json_extract(tags, '$') LIKE ?"
        params.append(f'%{tags[0]}%')
    
    query += " ORDER BY quality_score DESC"
    
    # Convert to fine-tuning format...
```

### Knowledge Graph (Phase 3)

- Relationships between knowledge entries
- "This pattern is used in these files"
- "This decision affects these modules"
- Graph visualization and traversal

---

## Testing Strategy

### Unit Tests

1. **Schema Migration**
   - Test v3 → v4 migration succeeds
   - Test idempotent migration (can run multiple times)
   - Test data integrity after migration

2. **Knowledge CRUD**
   - Create, read, update, delete knowledge entries
   - Test deduplication logic
   - Test confidence score updates

3. **FTS5 Search**
   - Test full-text search queries
   - Test search with filters (type, scope)
   - Test search ranking

4. **Session Summary Generation**
   - Test summary generation from session data
   - Test quality score calculation
   - Test auto-tagging

### Integration Tests

1. **End-to-End Session Flow**
   - Create session → Execute tools → End session → Summary generated
   - Verify all tables populated correctly

2. **Search API**
   - Test search endpoints with various queries
   - Test pagination and filtering
   - Test export functionality

### Performance Tests

1. **Search Performance**
   - Test with 1000+ knowledge entries
   - Test FTS5 query latency
   - Test concurrent search queries

2. **Insert Performance**
   - Test bulk knowledge insertion
   - Test tool execution logging overhead

---

## Rollback Plan

If migration fails or issues arise:

```bash
# 1. Stop the server

# 2. Restore backup
cp data/codeassist.db.backup.v3 data/codeassist.db

# 3. Restart server
# Schema will remain at v3
```

---

## Success Metrics

### Immediate (Phase 1)

- [ ] Schema migration completes without errors
- [ ] All existing sessions remain accessible
- [ ] Session summaries generated for new sessions
- [ ] FTS5 search returns relevant results
- [ ] Tool execution logging has <5ms overhead

### Short-term (1-2 weeks)

- [ ] Knowledge base contains 50+ entries
- [ ] Search finds relevant knowledge in <100ms
- [ ] Quality scores correlate with manual assessment
- [ ] No performance degradation in existing features

### Long-term (1-3 months)

- [ ] Knowledge base contains 500+ entries
- [ ] Q&A extraction pipeline running automatically
- [ ] Fine-tuning dataset export working
- [ ] User reports reduced context overhead

---

## Open Questions

1. **Summary Generation**: Should we use the current LLM client or a separate model for summaries?

2. **Quality Scoring**: What factors should weight most in quality_score?
   - Session duration?
   - Tool execution success rate?
   - User follow-up questions?
   - Manual rating?

3. **Knowledge Deduplication**: How aggressive should we be?
   - Exact match only?
   - Similar content detection?
   - Merge related entries?

4. **FTS5 vs Vector Search**: Should we implement both in Phase 1, or start with FTS5 only?

5. **Storage Limits**: Should we set maximums for:
   - `result_full` in tool_executions?
   - `content_preview` in file_snapshots?
   - Total knowledge entries?

---

## Appendix: A: Complete Schema Reference

### All v4 Tables

```sql
-- Session summaries
CREATE TABLE session_summaries (...);  -- See Section 1

-- Knowledge entries
CREATE TABLE knowledge_entries (...);  -- See Section 2

-- Tool executions
CREATE TABLE tool_executions (...);    -- See Section 4

-- LLM usage
CREATE TABLE llm_usage (...);          -- See Section 5

-- Session tags
CREATE TABLE session_tags (...);       -- See Section 6

-- File snapshots
CREATE TABLE file_snapshots (...);     -- See Section 7

-- Q&A pairs
CREATE TABLE qa_pairs (...);           -- See Section 8

-- FTS5 virtual tables
CREATE VIRTUAL TABLE knowledge_search USING fts5(...);      -- See Section 3
CREATE VIRTUAL TABLE session_summary_search USING fts5(...); -- See Section 3
```

### All Indexes

```sql
-- Session summaries
CREATE INDEX idx_session_summaries_session ON session_summaries(session_id);
CREATE INDEX idx_session_summaries_quality ON session_summaries(quality_score);

-- Knowledge entries
CREATE INDEX idx_knowledge_type ON knowledge_entries(entry_type);
CREATE INDEX idx_knowledge_scope ON knowledge_entries(scope, scope_identifier);
CREATE INDEX idx_knowledge_source ON knowledge_entries(source_session_id);
CREATE INDEX idx_knowledge_confidence ON knowledge_entries(confidence);

-- Tool executions
CREATE INDEX idx_tool_executions_session ON tool_executions(session_id);
CREATE INDEX idx_tool_executions_tool ON tool_executions(tool_name);
CREATE INDEX idx_tool_executions_success ON tool_executions(success);
CREATE INDEX idx_tool_executions_created ON tool_executions(created_at);

-- LLM usage
CREATE INDEX idx_llm_usage_session ON llm_usage(session_id);
CREATE INDEX idx_llm_usage_model ON llm_usage(model);
CREATE INDEX idx_llm_usage_created ON llm_usage(created_at);

-- Session tags
CREATE INDEX idx_session_tags_session ON session_tags(session_id);
CREATE INDEX idx_session_tags_tag ON session_tags(tag);

-- File snapshots
CREATE INDEX idx_file_snapshots_session ON file_snapshots(session_id);
CREATE INDEX idx_file_snapshots_path ON file_snapshots(file_path);
CREATE INDEX idx_file_snapshots_action ON file_snapshots(action);

-- Q&A pairs
CREATE INDEX idx_qa_pairs_session ON qa_pairs(session_id);
CREATE INDEX idx_qa_pairs_quality ON qa_pairs(quality_score);
CREATE INDEX idx_qa_pairs_success ON qa_pairs(success);
```

---

**Document Version**: 1.0  
**Last Updated**: 2026-07-20  
**Author**: CodeAssist Team
