# CodeAssist Knowledge Base - Quick Reference

**Phase 1: Core Knowledge Base**  
**Schema Version:** 4

---

## Overview

Transform CodeAssist from session-based to persistent knowledge base with:
- Human-readable SQLite storage
- Full-text search (FTS5)
- Semantic search (vector embeddings)
- Fine-tuning-ready data capture
- Complete audit trail

---

## New Tables (v4)

| Table | Purpose | Key Fields |
|-------|---------|------------|
| `session_summaries` | AI-generated session summaries | summary, key_topics, quality_score |
| `knowledge_entries` | Accumulated project knowledge | entry_type, scope, content, confidence |
| `tool_executions` | Complete tool audit trail | tool_name, arguments, duration_ms, success |
| `llm_usage` | Token/cost tracking | model, tokens, estimated_cost_usd |
| `session_tags` | Session categorization | tag, source (user/auto/system) |
| `file_snapshots` | File change tracking | file_path, action, content_hash |
| `qa_pairs` | Fine-tuning data capture | question, answer_summary, quality_score |

---

## Search Capabilities

### Full-Text Search (FTS5)
```sql
-- Search knowledge for "async patterns"
SELECT * FROM knowledge_search 
WHERE knowledge_search MATCH 'async pattern';

-- Search session summaries
SELECT * FROM session_summary_search 
WHERE session_summary_search MATCH 'database optimization';
```

### Semantic Search (Vector Embeddings)
```python
# Via API
GET /api/knowledge/semantic?q=error handling patterns
GET /api/knowledge/{id}/similar

# Auto-generate embeddings for existing entries
POST /api/knowledge/embeddings/generate
```

### SQL Queries
```sql
-- High-quality knowledge entries
SELECT * FROM knowledge_entries 
WHERE quality_score > 0.8 
ORDER BY confidence DESC;

-- Tool usage stats
SELECT tool_name, COUNT(*), AVG(duration_ms) 
FROM tool_executions 
GROUP BY tool_name;
```

---

## Human-Readable Access

1. **SQLite CLI**: Direct SQL queries
2. **DB Browser for SQLite**: Visual GUI
3. **Python scripts**: Programmatic access
4. **Export**: CSV, JSON formats

---

## Configuration

Add to `config.toml` for semantic search:
```toml
[llm]
embedding_model = "text-embedding-3-small"  # optional
```

If not set, falls back to text search automatically.

---

## Migration Command

```bash
# Backup existing database
cp data/codeassist.db data/codeassist.db.backup.v3

# Restart server (auto-migrates schema + creates FTS5 tables)
python server.py
```

**Note:** FTS5 tables are now automatically created on startup if they don't exist. No manual SQL step required.

---

## Implementation Phases

### Phase 1 (Complete)
- [x] Add v4 tables to schema
- [x] Implement FTS5 search
- [x] Session summary generation
- [x] Knowledge extraction (patterns, conventions, decisions)
- [x] Semantic search (vector embeddings)
- [x] Tool execution logging
- [x] LLM usage tracking

### Phase 2 (Complete)
- [x] Pattern detection for repetitive workflows
- [x] Auto-skill creation (when confidence > 0.7)
- [x] Custom tool creation system
- [x] Skills hot-reload
- [x] Custom tools dynamic loader
- [x] Management API endpoints

### Phase 3 (Future)
- [ ] CLI management commands
- [ ] Trust levels for custom tools
- [ ] Quality scoring refinement

---

## Example Queries

### Find knowledge about a file
```sql
SELECT * FROM knowledge_entries 
WHERE scope = 'file' AND scope_identifier = 'src/main.py';
```

### Search for patterns
```sql
SELECT * FROM knowledge_entries 
WHERE entry_type = 'pattern' 
AND content LIKE '%async%';
```

### Get session summary
```sql
SELECT s.name, k.summary, k.quality_score 
FROM session_summaries k 
JOIN sessions s ON k.session_id = s.id 
WHERE s.id = 'session-uuid';
```

### Export to CSV
```bash
sqlite3 -header -csv data/codeassist.db \
  "SELECT * FROM knowledge_entries;" > knowledge.csv
```

---

## API Endpoints

### Knowledge
```
GET  /api/knowledge                    - List entries (filter by type, scope)
GET  /api/knowledge/search?q=...       - Full-text search (FTS5)
GET  /api/knowledge/semantic?q=...     - Semantic search (embeddings)
GET  /api/knowledge/{id}               - Get entry
GET  /api/knowledge/{id}/similar       - Find similar entries
POST /api/knowledge                    - Create entry
PUT  /api/knowledge/{id}               - Update entry
DELETE /api/knowledge/{id}             - Delete entry
POST /api/knowledge/embeddings/generate - Batch generate embeddings
```

### Analytics
```
GET  /api/analytics/tools              - Tool usage stats
GET  /api/analytics/llm                - LLM usage stats
```

### Session Tags
```
GET  /api/sessions/search/tags?tags=... - Search sessions by tags
GET  /api/sessions/{id}/tags           - Get session tags
POST /api/sessions/{id}/tags           - Add session tag
```

### File History
```
GET  /api/files/history?file_path=...  - File modification history
```

### Self-Creation System
```
GET  /api/custom-tools                - List custom tools
POST /api/custom-tools/reload         - Reload custom tools from disk
GET  /api/skills/list                 - List all skills (built-in + custom)
POST /api/skills/reload               - Reload skills from disk
GET  /api/auto-creation/status        - Auto-creation status and stats
```

---

## Self-Creation System

CodeAssist can automatically create skills when it detects repetitive workflows.

### How It Works

1. **Pattern Detection** - Session hook analyzes tool call sequences
2. **Repetition Check** - Same sequence 3+ times triggers pattern storage
3. **Confidence Scoring** - Higher repetition = higher confidence
4. **Auto-Creation** - If confidence > 0.7, skill is auto-created
5. **KB Integration** - All creations logged to knowledge base

### Manual Creation

Use tools in chat:
- `create_skill` - Create a new skill file
- `create_tool` - Create a custom Python tool

### Configuration

```toml
[agent]
auto_create_skills = true      # Enable auto-creation
auto_create_tools = false      # Disabled by default (security)
max_auto_creations = 3         # Per session limit
min_confidence = 0.7           # Threshold for auto-creation
```

### Custom Tools Directory

Custom tools are stored in `.codeassist/custom_tools/`:

```python
# .codeassist/custom_tools/my_tool.py
TOOLS = {
    "my_tool": {
        "name": "my_tool",
        "description": "Does something useful",
        "parameters": {
            "type": "object",
            "properties": {
                "input": {"type": "string"}
            }
        }
    }
}

async def execute(input: str) -> str:
    return f"Processed: {input}"
```

---

## Quality Score Calculation

```python
def calculate_quality_score(session_data):
    factors = {
        'duration': min(session_data['duration'] / 3600, 1.0),  # Max 1 hour
        'tool_success': session_data['successful_tools'] / max(session_data['total_tools'], 1),
        'completeness': session_data['goals_achieved'] / max(session_data['goals_total'], 1),
        'user_satisfaction': session_data.get('satisfaction', 0.5),
    }
    
    weights = {
        'duration': 0.2,
        'tool_success': 0.3,
        'completeness': 0.3,
        'user_satisfaction': 0.2,
    }
    
    return sum(factors[k] * weights[k] for k in factors)
```

---

## Rollback

```bash
# Restore v3 database
cp data/codeassist.db.backup.v3 data/codeassist.db
```

---

**Full Design**: See `knowledge-base-schema.md`  
**Version**: 1.1  
**Date**: 2026-07-20
