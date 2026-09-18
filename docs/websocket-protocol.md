# CodeAssist WebSocket Protocol

**Endpoint:** `ws://<host>:<port>/ws/<session_id>`

## Authentication

If a `password` is configured in `config.toml`, the client must send it in the `Sec-WebSocket-Protocol` header:

```javascript
new WebSocket(`ws://host:port/ws/${sessionId}`, [password])
```

The password is NEVER accepted via query parameters (security best practice).

## Client → Server Messages

```jsonc
// Send a user message to the agent. Optional images (array of base64 data
// URLs, max 4, only accepted when the model is vision-capable) and files
// (array of {name, content}, max 5, inlined into the prompt as text parts;
// rejected with an error if too large).
{"type": "user_message", "content": "...", "images": [...], "files": [...]}

// Cancel the current agent turn
{"type": "cancel"}

// Undo the last agent turn (assistant + all tool messages since last user message)
{"type": "undo"}

// Rollback to a specific message (deletes all messages after it)
{"type": "rollback", "message_id": "uuid"}

// Respond to a tool confirmation prompt.
// remember: "always allow this tool" — persisted server-side, bound to the
// confirm_id's own tool + file_path (client tool/file_path values are ignored).
{"type": "confirm_response", "id": "...", "approved": true, "trust_workspace": false, "trust_shell": false, "trust_tool": false, "remember": false}

// Respond to a question from the agent
{"type": "question_response", "id": "...", "answer": "..."}

// Switch the active agent for this session (agent_name is the registry id/key,
// e.g. "default" or "research"; persisted per session across reconnects)
{"type": "switch_agent", "agent_name": "research"}

// Approve a dynamically loaded custom tool
{"type": "approve_tool", "file_path": "..."}

// Reject a dynamically loaded custom tool
{"type": "reject_tool", "file_path": "..."}
```

## Server → Client Messages

### Agent stream events (sent during agent processing)

```jsonc
// Streamed text content from the LLM
{"type": "text_delta", "content": "..."}

// LLM requested tool call(s)
{"type": "tool_call", "id": "...", "name": "...", "arguments": {...}}

// LLM finished a response
{"type": "finish", "reason": "stop", "usage": {"prompt_tokens": 123, "completion_tokens": 45}}

// Tool execution result
{"type": "tool_result", "id": "...", "name": "...", "output": "..."}

// Context usage info before compaction
{"type": "context", "tokens": 12345, "usage_pct": 85.2, "severity": "warning"}

// Context was compacted
{"type": "compacted", "message": "..."}

// Agent needs user confirmation
{"type": "confirm_request", "id": "...", "tool": "...", "arguments": {...}, "in_workspace": bool}

// Agent asks a question
{"type": "question_request", "id": "...", "question": "...", "options": [...], "required": bool}

// Plan/todo list update
{"type": "plan_update", "tasks": [...]}

// Error occurred
{"type": "error", "message": "..."}

// Turn complete
{"type": "done"}

// Turn was cancelled by the user
{"type": "cancelled"}

// Agent was switched
{"type": "agent_switched", "agent": {"id": "research", "name": "Research", "description": "...", "model": null}}

// Active agent for this session (sent once right after connect). The choice is
// persisted per session, so reconnecting to a session reports its agent.
{"type": "active_agent", "agent": {"id": "default", "name": "CodeAssist", ...}}
```

### Non-agent events

```jsonc
// Undo succeeded
{"type": "undo_done", "deleted": 5}

// Rollback succeeded
{"type": "rollback_done", "deleted": 3}

// Tool was approved
{"type": "tool_approved", "file_path": "..."}

// Tool was rejected
{"type": "tool_rejected", "file_path": "..."}

// Trust approval required for a new tool
{"type": "trust_approval_required", ...}
```

## Client State Model

1. Connect → WebSocket opened
2. Client sends `user_message` → agent starts processing
3. Agent sends stream events (`text_delta`, `tool_call`, `tool_result`, etc.)
4. Agent sends `done` → agent idle, client may send next `user_message`
5. Client may send `cancel` to interrupt processing at any time
6. `undo` removes the last assistant turn; `rollback` removes all messages after a specific point
