-- CodeAssist Knowledge Base - FTS5 Virtual Tables
-- Run after v4 schema migration
-- Usage: sqlite3 data/codeassist.db < fts5_tables.sql

-- Knowledge search index (full-text search across knowledge entries)
CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_search USING fts5(
    entry_id UNINDEXED,
    entry_type,
    content,
    tags,
    scope,
    scope_identifier
);

-- Session summary search index (full-text search across session summaries)
CREATE VIRTUAL TABLE IF NOT EXISTS session_summary_search USING fts5(
    summary_id UNINDEXED,
    session_id UNINDEXED,
    summary,
    key_topics,
    tools_used,
    files_modified
);

-- Populate from existing data (run once after migration)
-- Uncomment the following lines if you have existing data to index:

-- INSERT INTO knowledge_search(entry_id, entry_type, content, tags, scope, scope_identifier)
-- SELECT id, entry_type, content, tags, scope, scope_identifier
-- FROM knowledge_entries;

-- INSERT INTO session_summary_search(summary_id, session_id, summary, key_topics, tools_used, files_modified)
-- SELECT id, session_id, summary, key_topics, tools_used, files_modified
-- FROM session_summaries;

-- Example queries:

-- Search knowledge for "async pattern"
-- SELECT * FROM knowledge_search WHERE knowledge_search MATCH 'async pattern';

-- Search session summaries for "database optimization"
-- SELECT * FROM session_summary_search WHERE session_summary_search MATCH 'database optimization';

-- Search with ranking
-- SELECT *, rank FROM knowledge_search WHERE knowledge_search MATCH 'error handling' ORDER BY rank;

-- Boolean search
-- SELECT * FROM knowledge_search WHERE knowledge_search MATCH 'async AND (error OR exception)';

-- Prefix search
-- SELECT * FROM knowledge_search WHERE knowledge_search MATCH 'data*';
