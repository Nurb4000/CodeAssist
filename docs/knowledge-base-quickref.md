# CodeAssist Knowledge Base - Quick Reference

**Phase 1: Core Knowledge Base**  
**Schema Version:** 4

---

## Overview

Transform CodeAssist from session-based to persistent knowledge base with:
- Human-readable SQLite storage
- Full-text search (FTS5)
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

### FTS5 Full-Text Search
```sql
-- Search knowledge for "async patterns"
SELECT * FROM knowledge_search 
WHERE knowledge_search MATCH 'async pattern';

-- Search session summaries
SELECT * FROM session_summary_search 
WHERE session_summary_search MATCH 'database optimization';
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

## Implementation Phases

### Phase 1 (Current)
- [ ] Add v4 tables to schema
- [ ] Implement FTS5 search
- [ ] Session summary generation
- [ ] Basic knowledge extraction

### Phase 2 (Future)
- [ ] Tool execution logging
- [ ] LLM usage tracking
- [ ] Q&A extraction pipeline
- [ ] Quality scoring refinement

### Phase 3 (Future)
- [ ] Vector search (semantic similarity)
- [ ] Fine-tuning dataset export
- [ ] Knowledge graph

---

## Key Files to Modify

| File | Changes |
|------|---------|
| `session.py` | Add `_add_v4_tables()`, bump SCHEMA_VERSION |
| `server.py` | Add search/analytics endpoints |
| `knowledge.py` | **New** - Knowledge extraction pipeline |
| `qa_extractor.py` | **New** - Q&A extraction for fine-tuning |

---

## Migration Command

```bash
# Backup existing database
cp data/codeassist.db data/codeassist.db.backup.v3

# Restart server (auto-migrates schema + creates FTS5 tables)
python server.py
```

**Note:** FTS5 virtual tables are now automatically created on startup if they don't exist. No manual SQL step required.

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
**Version**: 1.0  
**Date**: 2026-07-20
