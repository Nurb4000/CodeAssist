# CodeAssist Feature Gap Plan

Generated: 2026-08-04
Reference: `/home/ziggy/Code-Projects/opencode` (upstream)
Project: `/home/ziggy/Code-Projects/CodeAssist` (ours)

---

## Executive Summary

After thorough review of both codebases, opencode has added several significant features since we last synced. Below are the missing features organized by priority and implementation complexity.

**Key findings:**
- CodeAssist already has 27 tools vs opencode's ~18 built-in tools. We lead on tool variety.
- opencode's biggest additions are: Plan Mode, Subagent System (task tool), Snapshot/Revert, advanced Compaction, Question system, Permission model, Instruction Discovery, and LSP Tool.
- CodeAssist leads on: Knowledge base, Cost tracking, Docker tool, Image analysis, Fossil VCS, SSRF protection, Dynamic tools, Custom Python tools.

---

## User Decisions (Recorded)

1. **Plan Mode**: Full 5-phase workflow (explore parallel → design → review → final plan → plan_exit)
2. **Subagents**: Include background mode from the start
3. **Snapshots**: Use separate hidden git repo approach (like opencode)
4. **Compaction**: Both options available, default to LLM-based summarization (better general use)

---

## Priority 1: High Impact, Reasonable Effort

### 1. Plan Mode Agent ✅ COMPLETE
**What:** A dedicated `plan` agent that operates in read-only mode. It can only explore code, ask questions, and write to a plan file (`.codeassist/plans/*.md`). After planning, the user approves and switches to a `build` agent that executes the plan.

**Full 5-phase workflow:**
1. **Phase 1: Initial Understanding** — Launch up to 3 explore subagents in parallel to efficiently explore the codebase. Use question tool to clarify ambiguities.
2. **Phase 2: Design** — Launch general agent(s) to design implementation approach based on Phase 1 results.
3. **Phase 3: Review** — Read critical files, ensure plans align with user intent, ask remaining questions.
4. **Phase 4: Final Plan** — Write final plan to `.codeassist/plans/{session_id}.md` (only file plan agent can edit).
5. **Phase 5: Call plan_exit** — Ask user for approval, switch to build agent if approved.

**Implementation:**
- [x] Add `plan` agent to `agents.py` with read-only permissions
- [x] Allow plan agent to write to `.codeassist/plans/*.md` only
- [x] Create `plan_exit` tool that asks user for approval, then switches to build agent
- [x] Add synthetic prompt injection in `agent.py` when switching from plan to build
- [x] Add plan mode reminder prompts (adapted from opencode)
- [x] Update WebSocket protocol: support `switch_agent` with plan/build transition
- [x] UI: visual indicator for plan mode, plan file preview in sidebar

**Effort:** 2-3 days ✅

---

### 2. Subagent System (Task Tool)
**What:** A `task` tool that lets the main agent spawn subagents with specialized capabilities. Supports foreground (wait for result) and background (notify on completion) modes. Enforces depth limits to prevent infinite nesting.

**Key agents:**
- `build` - primary agent with full tool access
- `plan` - read-only planning agent
- `general` - multi-step task execution (subagent)
- `explore` - fast codebase exploration (subagent, read-only tools only)
- `compaction` - hidden agent for context summarization

**Implementation:**
- [ ] Add `task` tool to `tools/` with parameters: `description`, `prompt`, `subagent_type`, `task_id` (resume), `background` (bool)
- [ ] Create subagent session management: child sessions linked to parent via `parent_id`
- [ ] Implement depth limit config (`subagent_depth` in config, default 1)
- [ ] Permission inheritance: derive child permissions from parent + agent type restrictions
- [ ] Background mode: async task execution with notification injection into parent session
- [ ] Foreground mode: wait for subagent result, inject into parent conversation
- [ ] Add `explore` and `general` agent types to `agents.py`
- [ ] Task result formatting: `<task id="..." state="completed|error|running">` XML structure
- [ ] Deny subagents from using `task` and `todowrite` unless explicitly permitted

**Effort:** 3-4 days

---

### 3. Snapshot and Revert System
**What:** Git-based snapshot tracking at session boundaries. Captures workspace state before/after each turn. Enables reverting changes made by the agent. Uses a separate hidden git repo to avoid polluting user's git history.

**Implementation:**
- [ ] Create `snapshot.py` module: track workspace state using git at session boundaries
- [ ] Use a separate hidden git repo (`.codeassist/snapshot/`) to avoid polluting user's git history
- [ ] Snapshot before each agent turn starts, after each turn completes
- [ ] Store snapshot hashes in session metadata
- [ ] Add `revert` capability: stage revert, preview diff, commit revert
- [ ] Compute file-level diffs between snapshots (additions, deletions, modifications)
- [ ] Session summary: aggregate diff stats per session (files changed, lines added/removed)
- [ ] Config option to disable snapshots (`snapshot = false`)
- [ ] Cleanup: prune old snapshots after configurable retention period

**Effort:** 3-4 days

---

### 4. Structured Question System
**What:** A proper question/answer protocol where the agent can ask the user structured questions with multiple-choice options, custom answers, and headers. Questions are persisted and trackable.

**Current CodeAssist state:** CodeAssist has a basic `QuestionTool` in `tools/advanced.py` but it's simple free-text.

**Implementation:**
- [ ] Enhance `QuestionTool` to support structured questions with:
  - `questions[]` array, each with `question`, `header`, `options[]`, `multiple` (bool)
  - Options: `label`, `description`
  - Custom answer option (always available)
- [ ] Persist pending questions in session state
- [ ] Add question rejection handling (user dismisses question)
- [ ] Format answers back to agent as: `"question"="answer1, answer2"`
- [ ] WebSocket events: `question_request` (enhanced), `question_response`, `question_rejected`
- [ ] UI: render structured questions with radio buttons / checkboxes

**Effort:** 1-2 days

---

### 5. LSP Tool
**What:** A tool that lets the agent query language servers for code intelligence operations.

**Current CodeAssist state:** CodeAssist has `lsp_client.py` but no LSP tool exposed to the agent.

**Implementation:**
- [ ] Create `lsp` tool in `tools/` with operation parameter
- [ ] Wire up existing `lsp_client.py` to support all 9 operations
- [ ] File existence check before LSP queries
- [ ] LSP server availability check per file type
- [ ] Result formatting: structured JSON output for agent consumption
- [ ] Permission: always allow (read-only operation)

**Effort:** 1-2 days

---

## Priority 2: Significant Impact, More Complex

### 6. Advanced Compaction with LLM Summaries
**What:** Use the LLM to generate structured summaries of conversation history when context is full. Preserves recent turns intact. Both text truncation and LLM summarization available as options.

**Current CodeAssist state:** Two-level text compaction (summarize tool outputs, then drop old messages). No LLM-based summarization.

**Implementation:**
- [ ] Add `compaction` agent type (hidden, read-only) to `agents.py`
- [ ] Create compaction prompt template (similar to opencode's SUMMARY_TEMPLATE)
- [ ] When context exceeds threshold:
  - **LLM mode (default):** Select head messages, send to LLM for compact summarization, replace head with summary message, preserve recent turns intact
  - **Text mode:** Existing two-level truncation (summarize tool outputs → drop old messages)
- [ ] Track compaction state per session (previous summary, tail start ID)
- [ ] Configurable: `compaction.mode` ("llm" or "text"), `compaction.model` (cheaper model), `compaction.tail_turns`, `compaction.preserve_recent_tokens`
- [ ] Auto-continue: after compaction, inject "Continue if you have next steps" prompt
- [ ] Overflow handling: if even compaction can't fit, strip media and retry

**Default:** LLM-based summarization (preserves more meaning, better for complex tasks)
**Fallback:** Text truncation (faster, cheaper, good for simple tasks)

**Effort:** 3-4 days

---

### 7. Managed Tool Output Files
**What:** When tool output exceeds configured limits, save the full output to a managed file and give the agent a truncated preview with a path hint. Automatic cleanup of old output files.

**Current CodeAssist state:** `truncate_tool_result()` in `tokens.py` truncates inline. No managed file storage.

**Implementation:**
- [ ] Create `tool_output_store.py` module
- [ ] Managed output directory: `.codeassist/tool-output/`
- [ ] When tool output exceeds limits:
  1. Write full output to timestamped file
  2. Return head/tail preview with path hint
  3. Hint text: "Full output saved to: {path}. Use Grep or Read with offset/limit."
- [ ] Configurable limits: `tool_output.max_lines`, `tool_output.max_bytes`
- [ ] Periodic cleanup: remove files older than retention period (default 7 days)
- [ ] If task tool is available, hint suggests delegating to explore subagent

**Effort:** 1 day

---

### 8. Permission System Enhancement
**What:** Granular, pattern-based permissions per agent. Supports "allow", "deny", "ask" actions with file path patterns. Saved permission preferences.

**Current CodeAssist state:** Basic trust flags (`_trust_workspace_writes`, `_trust_shell`) and `CONFIRM_TOOLS` set. No pattern-based permissions.

**Implementation:**
- [ ] Redesign agent permissions in `agents.py`:
  - Each agent has a permission ruleset: `{ tool_name: { pattern: action } }`
  - Actions: "allow", "deny", "ask"
  - Patterns: glob patterns for file paths (e.g., `"*.env": "ask"`)
- [ ] Permission merging: default rules + user-configured overrides
- [ ] Saved permissions: persist "always allow" choices per pattern
- [ ] Update `needs_confirmation()` in `agent.py` to use new permission model
- [ ] Config section for user permission overrides

**Effort:** 2-3 days

---

### 9. Instruction Discovery (AGENTS.md)
**What:** Automatic discovery of project instruction files by walking up the directory tree. Supports AGENTS.md, CLAUDE.md, and remote URLs. Instructions are injected into system context.

**Current CodeAssist state:** No instruction discovery. System prompt is static.

**Implementation:**
- [ ] Create `instruction_discovery.py` module
- [ ] On session start, walk up from workspace to find AGENTS.md / CLAUDE.md
- [ ] Also check global config directory for AGENTS.md
- [ ] Support remote instructions via HTTP URLs in config
- [ ] Inject discovered instructions into system prompt
- [ ] When reading a file, discover nested project instructions near that file
- [ ] Track which instructions have been loaded (avoid duplicates)
- [ ] Config: `instructions` list of paths/URLs, `disable_project_config` flag

**Effort:** 1-2 days

---

## Priority 3: Nice to Have

### 10. Session Sharing
**What:** Generate a shareable URL for a session conversation.

**Implementation:**
- [ ] Export session as self-contained JSON bundle
- [ ] Optional: integrate with a sharing backend (or just local file export)
- [ ] Config: `share` option ("disabled", "manual", "auto")

**Effort:** 1 day

---

### 11. Apply Patch Tool Review
**What:** Unified diff application tool, used by GPT models instead of edit/write.

**Current CodeAssist state:** Already has `apply_patch.py` tool. May need enhancement for model-specific behavior.

**Implementation:**
- [ ] Review existing `apply_patch.py` against opencode's implementation
- [ ] Ensure proper unified diff parsing and application
- [ ] Model-specific tool selection: GPT models get apply_patch, others get edit/write

**Effort:** 0.5 day (mostly review)

---

### 12. Skill System Review
**What:** Markdown-based skill files with frontmatter, slash commands, and dynamic discovery.

**Current CodeAssist state:** Already has a skill system in `codeassist/skills.py`. May need enhancement.

**Implementation:**
- [ ] Review existing skill system against opencode's approach
- [ ] Ensure skill guidance is injected into agent system prompt (available skills list)
- [ ] Skill tool: `list` and `get` actions (already implemented)
- [ ] Hot-reload skills when files change

**Effort:** 0.5 day (mostly review)

---

### 13. Plugin System Enhancement
**What:** Extensible plugin hooks for agents, commands, tools, skills, and model catalog.

**Current CodeAssist state:** Has basic plugin support in `codeassist/plugins.py`.

**Implementation:**
- [ ] Enhance plugin system with lifecycle hooks:
  - `agent.transform` - modify agent configurations
  - `tool.definition` - modify tool definitions
  - `session.compacting` - inject context during compaction
  - `chat.system.transform` - modify system prompt
- [ ] Plugin discovery and hot-reload

**Effort:** 2-3 days

---

### 14. Context Source System
**What:** Composable system context from multiple sources (date, environment, instructions, skills). Changes produce mid-conversation system messages.

**Implementation:**
- [ ] Create `system_context.py` module with composable context sources
- [ ] Built-in sources: date, environment, instructions, skills
- [ ] Context epoch tracking: when baseline changes, emit mid-conversation update
- [ ] Registry pattern for adding custom context sources via plugins

**Effort:** 2-3 days

---

## Implementation Order Recommendation

```
Phase A (Week 1): Foundation
  1. Plan Mode Agent          (2-3 days) ✅ COMPLETE
  2. Structured Question System (1-2 days)
  3. LSP Tool                 (1-2 days)

Phase B (Week 2): Core Features
  4. Subagent System          (3-4 days)
  5. Snapshot and Revert      (3-4 days)

Phase C (Week 3): Polish
  6. Advanced Compaction      (3-4 days)
  7. Managed Tool Output      (1 day)
  8. Permission Enhancement   (2-3 days)

Phase D (Week 4): Extras
  9. Instruction Discovery    (1-2 days)
  10. Session Sharing         (1 day)
  11. Apply Patch Review      (0.5 day)
  12. Skill System Review     (0.5 day)
  13. Plugin Enhancement      (2-3 days)
  14. Context Source System   (2-3 days)
```

**Total estimated effort: ~4 weeks of focused development**

---

## Features CodeAssist Already Has (No Action Needed)

- Knowledge base with semantic search and FTS5
- Cost tracking with real-time budget enforcement
- Docker container management tool
- Image analysis for vision-capable LLMs
- Fossil VCS support
- Database query tool
- Process management tool
- HTTP request tool
- Documentation generation tool
- Symbol search (ctags-based)
- Test runner with framework auto-detection
- Package manager detection and management
- Dynamic tool loading from Python files
- Custom user-written tools
- Web search via DuckDuckGo
- SSRF protection with DNS validation
- Session fork/export/import
- Multi-agent support (default, research, review)
- MCP client integration
- LSP client (infrastructure exists, just needs tool exposure)
- Plugin system (basic)
- Skills system (basic)
- Trust registry for tool approval
- Parallel tool execution via asyncio.gather()
- Two-level context compaction
- Streaming with WebSocket
- Markdown rendering with syntax highlighting

---

## Config Changes Needed

Add to `config.toml`:

```toml
[agent]
subagent_depth = 1                    # Max nested subagent depth
default_agent = "build"               # Default agent type

[compaction]
enabled = true
mode = "llm"                          # "llm" or "text"
tail_turns = 2                        # Recent turns to preserve intact
preserve_recent_tokens = 4000         # Token budget for recent context
model = ""                            # Optional: cheaper model for compaction

[tool_output]
max_lines = 2000
max_bytes = 51200                     # 50KB
retention_days = 7

[snapshot]
enabled = true                        # Enable git-based snapshots

[instructions]
paths = []                            # Additional instruction file paths/URLs
disable_project_config = false        # Disable AGENTS.md discovery
```

---

## Database Schema Changes Needed

```sql
-- Todo persistence (for subagent todo isolation)
CREATE TABLE IF NOT EXISTS todos (
    id TEXT PRIMARY KEY,
    session_id TEXT REFERENCES sessions(id),
    content TEXT NOT NULL,
    status TEXT NOT NULL,              -- pending, in_progress, completed, cancelled
    priority TEXT NOT NULL,            -- high, medium, low
    position INTEGER,
    created_at TEXT
);

-- Snapshot tracking
CREATE TABLE IF NOT EXISTS snapshots (
    id TEXT PRIMARY KEY,
    session_id TEXT REFERENCES sessions(id),
    turn_number INTEGER,
    git_hash TEXT,
    created_at TEXT
);

-- Permission saves
CREATE TABLE IF NOT EXISTS permission_saves (
    id TEXT PRIMARY KEY,
    tool_name TEXT NOT NULL,
    pattern TEXT NOT NULL,
    action TEXT NOT NULL,              -- allow, deny, ask
    created_at TEXT
);

-- Question persistence
CREATE TABLE IF NOT EXISTS questions (
    id TEXT PRIMARY KEY,
    session_id TEXT REFERENCES sessions(id),
    message_id TEXT,
    tool_call_id TEXT,
    questions TEXT,                    -- JSON array of question objects
    answers TEXT,                      -- JSON array of answer arrays
    status TEXT NOT NULL,              -- pending, answered, rejected
    created_at TEXT
);
```

---

## Notes

- opencode uses TypeScript/Effect framework. Our Python implementation should mirror the behavior, not the architecture.
- Many opencode features are deeply integrated with their event-sourcing system (V2). We can implement simpler versions that achieve the same user-facing behavior.
- The plan mode workflow is the single most valuable feature to port - it fundamentally changes how the agent approaches tasks.
- Subagent system enables parallel exploration and background work - high value for complex tasks.
- Snapshot/revert gives users confidence to let the agent make changes - critical for adoption.
- LLM-based compaction is the default because it preserves more semantic meaning, but text truncation remains as a fast/cheap fallback option.
