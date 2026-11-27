# CodeAssist WebSocket Protocol

**Endpoint:** `ws://<host>:<port>/ws/<session_id>`

## Authentication

If a `password` is configured in `config.toml`, the client must send it in the `Sec-WebSocket-Protocol` header:

```javascript
new WebSocket(`ws://host:port/ws/${sessionId}`, [password])
```

The password is NEVER accepted via query parameters (security best practice).

## Protocol version

Immediately after connecting, the server sends a capability frame before any
turn starts:

```jsonc
{"type": "protocol", "version": 2, "delivery": ["queue", "steer"]}
```

Version 2 adds explicit follow-up delivery (`queue` or `steer`). Clients that
omit `delivery` are still treated as `queue`, so older clients keep working.

## Client → Server Messages

```jsonc
// Send a user message to the agent. Optional images (array of base64 data
// URLs, max 4, only accepted when the model is vision-capable) and files
// (array of {name, content}, max 5, inlined into the prompt as text parts;
// rejected with an error if too large).
// `delivery` is "queue" (default): run after any current turn finishes, in order.
// `delivery` is "steer": inject into a running turn at its next loop boundary,
// after all tool results for the current step have been persisted. If no turn
// is running, a steer is simply run as a normal queued turn.
{"type": "user_message", "content": "...", "delivery": "queue", "images": [...], "files": [...]}

// Cancel the current agent turn
{"type": "cancel"}

// Undo the last agent turn (assistant + all tool messages since last user message)
{"type": "undo"}

// Rollback to a specific message (deletes all messages after it)
{"type": "rollback", "message_id": "uuid"}

// Respond to a tool confirmation prompt.
// remember: "always allow this tool" — persisted server-side, bound to the
// confirm_id's own tool + file_path (client tool/file_path values are ignored).
// trust_all: "trust all tools for this session" (session-scoped, ephemeral).
{"type": "confirm_response", "id": "...", "approved": true, "trust_workspace": false, "trust_shell": false, "trust_tool": false, "trust_all": false, "remember": false}

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

// Context usage. Emitted before compaction, and again after it when the
// request actually shrank (tokens is the size of the next request).
{"type": "context", "tokens": 12345, "usage_pct": 85.2, "severity": "warning"}

// Compaction started. Emitted immediately before the (silent, potentially slow)
// summarizer request so clients can show an in-progress indicator. `reason` is
// "context_window", "budget_rollover" or "forced". Not every "compacting" is
// followed by "compacted": the latter only fires when the request shrank.
{"type": "compacting", "message": "...", "reason": "context_window", "mode": "llm", "tokens": 12345, "usage_pct": 85.2}

// Context was compacted. tokens_before/tokens_after make the reduction
// visible; clients must not assume this fires when nothing shrank.
{"type": "compacted", "message": "...", "mode": "llm", "count": 1, "tokens_before": 12345, "tokens_after": 4567}

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

// A steer was accepted. `deferred: true` means no live turn existed at that
// moment, so it was queued as an ordinary follow-up instead.
{"type": "steer_queued", "content": "...", "deferred": false}

// A staged steer reached the agent loop boundary and was written to the
// transcript. The next model request sees it as the newest user message.
{"type": "steered", "content": "..."}
```

A steer is discarded if the turn is cancelled before its next loop boundary:
there is no later step to inject into, and the follow-up must not silently
start a new turn after Stop.

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
