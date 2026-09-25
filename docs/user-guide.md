# CodeAssist User Guide

A point-of-use walkthrough of the CodeAssist web interfaces — the chat workspace, the Admin &
Settings page, the Knowledge Base dashboard, and the Tool Manager. This is **how to use** the app
day to day.

For setup, configuration, and exhaustive reference tables (the full built-in tool list, agent
permission matrix, and skill slash-commands), see the top-level [`README.md`](../README.md). That
document is the overview; this guide is the walkthrough.

---

## Table of contents

1. [First run and signing in](#first-run-and-signing-in)
2. [The chat workspace at a glance](#the-chat-workspace-at-a-glance)
3. [Sessions: create, switch, rename, pin, delete](#sessions-create-switch-rename-pin-delete)
4. [Switching agents (the mode selector)](#switching-agents-the-mode-selector)
5. [Sending messages and attaching files](#sending-messages-and-attaching-files)
6. [What happens during a turn](#what-happens-during-a-turn)
7. [Thinking blocks (reasoning models)](#thinking-blocks-reasoning-models)
8. [Footer telemetry: model, context, tokens](#footer-telemetry-model-context-tokens)
9. [Import and export sessions](#import-and-export-sessions)
10. [Admin & Settings page](#admin--settings-page)
11. [Knowledge Base GUI](#knowledge-base-gui)
12. [Tool Manager](#tool-manager)
13. [Skills in chat](#skills-in-chat)
14. [Keyboard shortcuts & small behaviors](#keyboard-shortcuts--small-behaviors)

---

## First run and signing in

Start the server (see the README Quick Start) and open the URL it prints, e.g.
<http://localhost:8090>. You land on the **chat workspace**.

- **No password set** (the default): the page loads directly. Anyone who can reach the port can
  use the app, so set a password before exposing it beyond `localhost` — see "Securing the server"
  in the README.
- **Password set** (`[server] password` in `config.toml`): the browser shows its native HTTP Basic
  login prompt on first request. Enter the username/password once and it is reused for the page and
  the underlying WebSocket/API calls.

There is no custom login page — authentication is the browser's built-in basic-auth dialog.

## The chat workspace at a glance

The chat screen (`index.html`) is two columns:

```
┌──────────────────┬───────────────────────────────────────────┐
│ Sidebar (left)   │ Main chat area                              │
│                  │                                             │
│  • Header icons  │  • Message stream (user / assistant / tools)│
│  • Session list  │  • Plan / todo display                      │
│  • Footer        │  • Mode selector + attach + input bar       │
└──────────────────┴───────────────────────────────────────────┘
```

**Header icons (top-right of the sidebar)** — left to right:

| Icon | Opens |
|------|-------|
| 🔧 Wrench | Tool Manager |
| ⚙️ Gear | Admin / Settings |
| 📚 Book | Knowledge Base |
| ⬇️ Import | Import session dialog |
| `+` | New session |

**Sidebar footer** shows your model/context, a running token count with live rate, and the
"Show/Hide thinking" toggle (see [Footer telemetry](#footer-telemetry-model-context-tokens) and
[Thinking blocks](#thinking-blocks-reasoning-models)).

## Sessions: create, switch, rename, pin, delete

Each conversation is a **session**. Sessions are listed in the sidebar; the list reloads live as you
work.

- **New session** — click the `+` button in the sidebar header. The current session is preserved;
  a fresh chat opens.
- **Switch session** — click a session's name (or anywhere on its row) to switch to it. You cannot
  switch while a turn is streaming; finish or stop the current turn first.
- **Auto-title** — a new session is named "Untitled" until your first message; CodeAssist then
  replaces it with a short title derived from what you said.
- **Rename** — hover the session row to reveal four icons on its right: **pin**, **rename** (pencil),
  **export**, **delete** (trash). Click the pencil to rename inline (press Enter to save, Escape to
  cancel). Renames are saved to the session.
- **Pin** — click the pin icon to pin a session to the top of the list; click again to unpin. Pinned
  sessions sort above unpinned ones.
- **Summary preview** — if a session has an AI-generated summary, a truncated line appears under its
  name. Hover the truncation to read the full summary.
- **Delete** — click the trash icon. The session is removed from the list; if it was the open
  session, the chat area returns to the welcome screen.

## Switching agents (the mode selector)

The input bar has a **mode selector** button (labelled `Full` by default). It shows the current
agent and its short label. Click it to open a dropdown listing every available agent:

- Each entry shows the agent's short label and, when present, its description.
- The active agent is highlighted. Click another to switch.
- Switching sends a `switch_agent` request over the WebSocket; the new agent takes effect on the
  **next** turn. If a turn is already streaming, the switch is blocked with a message — wait for it
  to finish.
- The **Compaction** agent (used internally for context summarization) is hidden from the list.
- Your choice is **per session** and persists across reloads and reconnects.

Agents are configured in the Admin page (see [Admin & Settings page](#admin--settings-page)); custom
agents you create there appear in this dropdown alongside the built-in ones. See the README's
"Agent Types" section for what each built-in agent does and which tools it may use.

## Sending messages and attaching files

Type your message in the input bar at the bottom and:

- Press **Enter** to send. Use **Shift+Enter** for a line break.
- The **Send** button (▶) sends; while a turn is running it is replaced by the **Stop** button
  (■). Click Stop to end the turn early.

### Attaching files and images

Click the **attach** (paperclip) button to add files to your next message. Attachments appear as
chips/thumbnails above the input bar; click the `×` on any of them to remove it before sending.

- **Images** (PNG, JPEG, WebP, GIF) are sent to the model as vision input — but **only if the
  active model is vision-capable**. Vision capability is auto-detected from the backend (`/v1/models`)
  or taken from the `[llm] vision` setting in `config.toml`. If the model does not support images,
  attaching one shows a clear "this model doesn't support images" message; you can still attach text
  files.
- **Text files** (any `text/*` MIME type or common source/markup extension) are inlined into the
  prompt as an `[Attached file: name]` part, so the agent can read them directly.

**Limits:** up to **4 images** (max **8 MB** each) and up to **5 text files** (max **256 KB** each)
per message. Exceeding a limit shows an error and the offending file is not added.

## What happens during a turn

After you send a message, the agent works through it using tools. You watch this live:

- **Tool calls** appear in a collapsible tool panel in the message stream (name, arguments, and
  output). Expanding a tool call shows its full result; long outputs are truncated with a toggle to
  expand.
- **Confirmation prompts** — destructive operations (file writes/edits, shell commands, and others)
  pause for your approval unless the workspace is already trusted. The dialog offers:
  - **Allow once**, or **Always allow <tool> for this session** (session-scoped trust), and
  - **Always allow (remember permanently)** — persists the choice across sessions via the trust
    registry.
- **The agent can ask you a question.** Some tools (the `question` tool) pause the turn and open a
  dialog with options or a free-text field. Answer it and the turn resumes; the dialog stays open
  until answered.
- **Context usage bar** — the sidebar footer tracks how much of the context window is used. When a
  session approaches its limit, CodeAssist automatically **compacts** context (summarizing old tool
  outputs and messages) so long conversations keep running. See "Context window management" in the
  README for the thresholds.
- **Continue** — when a multi-step task finishes (or needs more input), a Continue control lets you
  prompt the agent to carry on without re-typing full instructions.

## Thinking blocks (reasoning models)

Reasoning models emit a "thinking" section before their final answer. Each thinking block is a
collapsible `<details>` labelled **🤖 Thinking…** inside the assistant message — click the triangle
to expand or collapse it individually.

A global toggle in the **sidebar footer** ("Show thinking" / "Hide thinking") flips every thinking
block at once. Your choice is remembered (in `localStorage`) across page loads.

## Footer telemetry: model, context, tokens

The sidebar footer shows two live readouts:

- **Model line** — the effective model name followed by its context window in parentheses, then the
  workspace path. Example: `gpt-4o (128,000) | /workspace/MyProject`. If the model was
  **auto-detected** from a local backend, a `• auto` badge is appended and hovering shows the
  detected model id.
- **Token line** — cumulative tokens used in the current session plus a live rate (e.g. `· 12.4
  tok/s`) while the model is streaming.

## Import and export sessions

Sessions are portable as JSON, which is useful for backups, sharing, or moving work between
machines.

- **Export** — hover a session and click the **export** icon (or use the header import button's
  sibling flow). A dialog lets you check **Redact PII** to strip personal data from the bundle, then
  downloads a self-contained JSON file. Reasoning content round-trips through export/import.
- **Import** — click the **Import** button in the sidebar header. Paste JSON or choose a `.json`
  file, optionally name the imported session, and confirm. Importing **creates a new session** and
  leaves your current chat untouched; a toast confirms success or failure.

## Admin & Settings page

Open it via the ⚙️ icon in the sidebar header (or the "Admin" link in the footer). This is the
control center for configuration and registries.

### Layout and navigation

- A left nav lists the sections: **Skills, MCP servers, LSP servers, Plugins, Custom tools, Agents,
  Settings**. Clicking a section expands it if collapsed, smooth-scrolls to it, and flashes the
  heading so you see where you landed.
- Every section is **collapsible** with a chevron toggle and shows a live **item count**
  (e.g. `Skills (16)`). Collapse state is remembered per section.
- **← Back to chat** in the header returns to the chat workspace; the **Refresh** button (⟳) re-reads
  everything from the server/DB.

### Settings tab

The Settings tab exposes UI-managed overrides that layer on top of `config.toml`. Settings are
grouped (LLM, Server, Agent, Tools, Features). For each setting you see:

- Its label and description.
- A **source badge**: `overridden` (a value set here in the UI) or `from config.toml` (the file's
  value, or the built-in default).
- A **restart-required** note when applicable — such changes take effect after the server restarts.

To change a value, edit it in place and click **Save** (top-right). Use the per-setting **Reset**
button to drop your override and revert to the config.toml/default value. Click **Test LLM
connection** to ping the backend using the base URL and API key entered in the form; it reports how
many models were returned or why the connection failed.

> Note: feature toggles (skills, plugins, MCP, LSP, git) are surfaced here, but enabling them still
> requires a server restart — those subsystems boot once at startup. A live hot-reload for these is
> a planned follow-up.

### Registry tabs

Each registry tab lists items in a table with an **Add/Create** form and per-item actions.

| Section | What you can do |
|---------|-----------------|
| **Skills** | View skills (name, description, slash command, source). **Reload skills from disk** to pick up new/changed files without restart. **Create** a skill from the form (name, description, slash command, content). |
| **MCP servers** | **Add** a server (name + config JSON). **Edit** any server via a modal (name, config, enabled toggle). **Delete** with confirmation. Requires MCP enabled in config. |
| **LSP servers** | **Add** a server (name, command, args JSON, languages JSON). **Edit** via modal (name, command, args, languages, enabled). **Delete** with confirmation. |
| **Plugins** | Read-only listing (name, version, enabled). Plugins load from disk; manage them by editing the plugin files and using **Reload**. |
| **Custom tools** | Listing of tools discovered in `runtime/custom_tools/`. **Reload custom tools** to re-scan the directory without restart. |
| **Agents** | View agents (key, name, description, model). **Create** a custom agent (key, description, model, instructions). **Edit** custom agents via modal (description, instructions, model, max iterations). **Delete** custom agents with confirmation. Built-in agents (`default`, `research`, `review`, `build`, …) are marked `built-in` and cannot be edited or deleted. |

The edit modal is a reusable inline dialog: fill in the fields, click **Save**, and the registry
reloads to reflect the change. JSON fields (MCP config, LSP args/languages) accept pretty-printed or
compact JSON and are validated on save.

## Knowledge Base GUI

Open it via the 📚 icon (or the "Knowledge Base" footer link). The Knowledge Base is CodeAssist's
persistent memory: it summarizes sessions, extracts reusable knowledge (patterns, conventions,
decisions), and makes it searchable.

The dashboard (`kb.html`) is organized into tabs:

| Tab | Purpose |
|-----|---------|
| **Dashboard** | Overview stats, entry counts, recent activity |
| **Entries** | Browse, filter, edit, and delete knowledge entries |
| **Search** | Full-text (and semantic, if enabled) search across all knowledge |
| **Sessions** | Session history with their AI-generated summaries |
| **Analytics** | Tool-usage charts and LLM cost/token tracking |
| **PII Manager** | Scan entries for personal data (emails, IPs, keys, …) and redact or delete |
| **Settings** | Configure auto-creation, confidence thresholds, and extraction behavior |
| **Export/Import** | Download or restore the knowledge base as JSON; clear it |

Semantic search is optional and needs an embedding model configured (`[llm] embedding_model` in
`config.toml`). Full feature detail lives in the README's "Knowledge Base" sections.

## Tool Manager

Open it via the 🔧 icon (or the "Tool Manager" footer link). The Tool Manager (`tools.html`) lets you
inspect and govern what the agent may do:

| Tab | Purpose |
|-----|---------|
| **All Tools** | Browse the built-in and custom tools available to the agent |
| **Custom Tools** | Review custom tools, trust/untrust them, and delete them |
| **Security Scan** | Scan custom tools for dangerous patterns (network access, subprocess calls, file writes, …) |
| **Usage Stats** | Tool usage statistics |

Custom tools are Python files in `runtime/custom_tools/` and are scanned for risky behavior
before you trust them. Built-in tools (`read`, `write`, `edit`, `shell`, `git`, `grep`, `webfetch`,
`todo`, and more) are documented in the README's "Built-in tools" table.

## Skills in chat

Skills are reusable, guided workflows invoked from chat:

- **By slash command** — type the skill's slash command (for example `/review`, `/refactor`,
  `/debug`) and press Enter.
- **By mention** — just describe what you want in plain language (for example "review this code" or
  "help me debug this"); the agent matches it to a skill when appropriate.

Built-in coding skills (code-review, refactor, debug, test, explain, document, optimize, clean,
security, convert, generate, migrate, lint) and non-coding examples (music, imagegen) ship with
CodeAssist. Add your own as markdown files in `runtime/skills/` — see the README's "Skills"
section for the frontmatter format. Custom and auto-created skills appear in the Admin page under
**Skills**.

## Keyboard shortcuts & small behaviors

| Action | How |
|--------|-----|
| Send a message | **Enter** in the input bar |
| New line in the input | **Shift+Enter** |
| Stop a running turn | The **■ Stop** button (replaces Send while streaming) |
| Rename a session | Hover the row → click the **pencil** → Enter to save, Escape to cancel |
| Pin / unpin a session | Hover the row → click the **pin** icon |
| Export a session | Hover the row → click the **export** icon |
| Delete a session | Hover the row → click the **trash** icon |
| Switch agent | Click the **mode selector** in the input bar |
| Show/hide all thinking | Sidebar footer **Show/Hide thinking** button |
| Refresh all admin data | Admin header **⟳** button |
| Jump to an admin section | Click a link in the admin sidebar nav |
