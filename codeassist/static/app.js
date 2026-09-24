const messagesEl = document.getElementById('messages');
const inputEl = document.getElementById('user-input');
const sendBtn = document.getElementById('send-btn');
const stopBtn = document.getElementById('stop-btn');
const sessionListEl = document.getElementById('session-list');
const newSessionBtn = document.getElementById('new-session-btn');
const modelInfoEl = document.getElementById('model-info');
const planDisplayEl = document.getElementById('plan-display');
const attachBtn = document.getElementById('attach-btn');
const fileInputEl = document.getElementById('file-input');
const attachPreviewEl = document.getElementById('attach-preview');
const inputAreaEl = document.getElementById('input-area');
// Short display labels for built-in agents (shown when the selector is collapsed).
const AGENT_SHORT = {
    default: 'Full', research: 'Research', review: 'Review',
    build: 'Build', general: 'General', explore: 'Explore',
};

function agentShortLabel(id) {
    return AGENT_SHORT[id] || id || 'Agent';
}

// Inline SVG icons (crisp + consistently rendered; inherit color via currentColor).
const ICONS = {
    import: `<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19V7"/><path d="M8 11l4-4 4 4"/><path d="M5 21h14"/></svg>`,
    export: `<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 5v12"/><path d="M8 9l4 4 4-4"/><path d="M5 21h14"/></svg>`,
    rename: `<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 20h4L20 8l-4-4L4 16z"/><path d="M13 5l4 4"/></svg>`,
    trash: `<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 7h16"/><path d="M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/><path d="M6 7l1 13a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1l1-13"/></svg>`,
    pin: `<svg viewBox="0 0 24 24" width="15" height="15" fill="currentColor" stroke="currentColor" stroke-width="1.5"><path d="M12 3l2 5 5 1-4 4 1 6-5-3-5 3 1-6-4-4 5-1z"/></svg>`,
};

let configData = {};
let pendingImages = [];
let pendingFiles = [];
let visionCapable = false;
let currentAgentName = null;

const MAX_IMAGES_PER_MESSAGE = 4;
const MAX_IMAGE_BYTES = 8 * 1024 * 1024;
const MAX_FILES_PER_MESSAGE = 5;
const MAX_FILE_BYTES = 256 * 1024;

let currentSessionId = null;
let ws = null;
let wsConnected = false;
let isStreaming = false;
let currentContentEl = null;

// Running token accounting for the sidebar footer (cumulative + rate).
let tokenState = { total: 0, lastTotal: 0, lastTime: null };
let textBuffer = '';
let reconnectTimer = null;
// Keepalive for the idle WebSocket. uvicorn itself never closes an idle socket,
// but intermediate proxies/browsers sometimes do; a periodic ping keeps the
// connection warm so the UI doesn't flash "Disconnected - reconnecting".
let pingTimer = null;
const PING_INTERVAL_MS = 20000;
let currentToolPanel = null;
let toolCallCount = 0;
let currentReasoningEl = null;

marked.setOptions({
    highlight: (code, lang) => {
        if (lang && hljs.getLanguage(lang)) {
            return hljs.highlight(code, { language: lang }).value;
        }
        return hljs.highlightAuto(code).value;
    },
    breaks: true,
});

function highlightToolOutput(text) {
    if (!text) return '';
    const escaped = escapeHtml(text);
    if (text.includes('\n') || text.startsWith('Error') || text.match(/^[{\[]/)) {
        try {
            if (text.trim().startsWith('{') || text.trim().startsWith('[')) {
                return '<pre><code>' + hljs.highlight(escapeHtml(JSON.stringify(JSON.parse(text), null, 2)), {language: 'json'}).value + '</code></pre>';
            }
        } catch(e) {}
        return '<pre><code>' + (hljs.highlightAuto(escaped).value) + '</pre></code>';
    }
    return escaped;
}

async function api(method, path, body) {
    const opts = { method, headers: { 'Content-Type': 'application/json' } };
    if (body) opts.body = JSON.stringify(body);
    const res = await fetch(path, opts);
    return res.json();
}

async function loadConfig() {
    configData = await api('GET', '/api/config');
    const effModel = configData.effective_model || configData.model;
    const effWindow = configData.effective_context_window || configData.context_window;
    // Show just the model id (basename) in the footer for readability.
    const displayModel = String(effModel || '(unknown)').split('/').pop();
    let modelLabel = displayModel;
    if (effWindow) {
        modelLabel += ` (${Number(effWindow).toLocaleString()})`;
    }
    if (configData.backend_source === 'backend') {
        modelLabel += ` • auto`;
        modelInfoEl.title = `Auto-detected from local backend: ${effModel}`;
    } else {
        modelInfoEl.title = `Configured model: ${configData.model}${configData.backend_external ? ' (external provider)' : ''}`;
    }
    modelInfoEl.textContent = `${modelLabel} | ${configData.workspace}`;
    visionCapable = !!configData.vision_capable;
    setAttachmentUiEnabled(true);
    await loadAgents();
}

async function loadAgents() {
    const agents = await api('GET', '/api/agents');
    const listEl = document.getElementById('mode-list');
    if (!listEl) return;
    listEl.innerHTML = '';
    for (const a of agents) {
        if (a.id === 'compaction' || a.name === 'compaction') continue;
        const item = document.createElement('div');
        item.className = 'mode-option';
        item.dataset.id = a.id;
        item.setAttribute('role', 'option');
        let html = `<div class="mode-option-name">${escapeHtml(agentShortLabel(a.id))}</div>`;
        if (a.description) html += `<div class="mode-option-desc">${escapeHtml(a.description)}</div>`;
        item.innerHTML = html;
        item.onclick = () => switchAgent(a.id);
        listEl.appendChild(item);
    }
    setAgentSelection(configData.agent_name || 'default');
    wireModeSelector();
}

function setAgentSelection(id) {
    currentAgentName = id;
    const label = document.getElementById('mode-label');
    if (label) label.textContent = agentShortLabel(id);
    document.querySelectorAll('.mode-option').forEach((o) => {
        o.classList.toggle('active', o.dataset.id === id);
    });
}

function wireModeSelector() {
    const trigger = document.getElementById('mode-trigger');
    const listEl = document.getElementById('mode-list');
    if (!trigger || !listEl || listEl.dataset.wired) return;
    listEl.dataset.wired = '1';
    const close = () => {
        listEl.hidden = true;
        trigger.setAttribute('aria-expanded', 'false');
    };
    const open = () => {
        listEl.hidden = false;
        trigger.setAttribute('aria-expanded', 'true');
        positionList();
    };
    // Re-anchor on resize so the list stays on-screen at any viewport height.
    window.addEventListener('resize', () => {
        if (!listEl.hidden) positionList();
    });
    const positionList = () => {
        const rect = trigger.getBoundingClientRect();
        const needed = Math.min(listEl.offsetHeight, 360);
        const below = window.innerHeight - rect.bottom;
        const above = rect.top;
        if (below < needed && above >= 120) {
            // Not enough room below — flip upward and fit within available space,
            // never taller than the 360px internal-scroll cap.
            listEl.classList.add('flip');
            listEl.style.maxHeight = Math.min(360, Math.max(120, above - 8)) + 'px';
        } else {
            listEl.classList.remove('flip');
            listEl.style.maxHeight = '';
        }
    };
    trigger.addEventListener('click', (e) => {
        e.stopPropagation();
        listEl.hidden ? open() : close();
    });
    document.addEventListener('click', (e) => {
        if (!listEl.contains(e.target) && e.target !== trigger) close();
    });
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') close();
    });
}

async function switchAgent(id) {
    if (id === currentAgentName) return;
    if (isStreaming) {
        showError('Agent is busy, switch when the current turn finishes');
        return;
    }
    const listEl = document.getElementById('mode-list');
    if (listEl) listEl.hidden = true;
    if (listEl) listEl.querySelector('.mode-option.active')?.classList.remove('active');
    if (ws && wsConnected) {
        ws.send(JSON.stringify({ type: 'switch_agent', agent_name: id }));
    } else {
        setAgentSelection(id);
    }
}

function setAttachmentUiEnabled(enabled) {
    if (!attachBtn) return;
    attachBtn.style.display = enabled ? 'flex' : 'none';
}

function setAttachmentUiBusy(busy) {
    if (!attachBtn) return;
    attachBtn.disabled = busy;
    attachBtn.style.opacity = busy ? '0.5' : '1';
}

function readFileAsDataURL(file) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result);
        reader.onerror = () => reject(reader.error);
        reader.readAsDataURL(file);
    });
}

const TEXT_FILE_EXTS = new Set(['txt','md','markdown','py','js','jsx','ts','tsx','json','toml','yaml','yml','csv','html','css','scss','sql','sh','bash','go','rs','java','c','cpp','h','hpp','rb','php','lua','xml']);

function isTextFile(file) {
    return file.type.startsWith('text/') || (file.name.split('.').pop() || '').toLowerCase() in TEXT_FILE_EXTS;
}

function readFileAsText(file) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result);
        reader.onerror = () => reject(reader.error);
        reader.readAsText(file);
    });
}

async function addPendingFile(file) {
    if (file.size > MAX_FILE_BYTES) {
        showError(`File '${file.name}' is too large (${(file.size / 1024).toFixed(0)}KB). Maximum is ${MAX_FILE_BYTES / 1024}KB`);
        return;
    }
    if (pendingFiles.length >= MAX_FILES_PER_MESSAGE) {
        showError(`Too many files. Maximum is ${MAX_FILES_PER_MESSAGE} per message`);
        return;
    }
    const content = await readFileAsText(file);
    pendingFiles.push({ name: file.name, content });
    renderPendingImages();
}

function removePendingFile(index) {
    pendingFiles.splice(index, 1);
    renderPendingImages();
}

async function addPendingImage(file) {
    if (!visionCapable) {
        showError('This model/server does not support images. You can still attach text files.');
        return;
    }
    if (!file.type.startsWith('image/') || !['image/png', 'image/jpeg', 'image/webp', 'image/gif'].includes(file.type)) {
        showError(`Unsupported image type '${file.type || 'unknown'}'. Supported: PNG, JPEG, WebP, GIF`);
        return;
    }
    if (file.size > MAX_IMAGE_BYTES) {
        showError(`Image too large (${(file.size / (1024 * 1024)).toFixed(1)}MB). Maximum is ${MAX_IMAGE_BYTES / (1024 * 1024)}MB`);
        return;
    }
    if (pendingImages.length >= MAX_IMAGES_PER_MESSAGE) {
        showError(`Too many images. Maximum is ${MAX_IMAGES_PER_MESSAGE} per message`);
        return;
    }
    const dataUrl = await readFileAsDataURL(file);
    pendingImages.push(dataUrl);
    renderPendingImages();
}

function removePendingImage(index) {
    pendingImages.splice(index, 1);
    renderPendingImages();
}

function renderPendingImages() {
    attachPreviewEl.innerHTML = '';
    pendingFiles.forEach((file, i) => {
        const chip = document.createElement('div');
        chip.className = 'attach-chip';
        const label = document.createElement('span');
        label.textContent = file.name;
        label.title = file.name;
        const remove = document.createElement('button');
        remove.className = 'attach-remove';
        remove.innerHTML = '&times;';
        remove.title = 'Remove file';
        remove.onclick = () => removePendingFile(i);
        chip.appendChild(label);
        chip.appendChild(remove);
        attachPreviewEl.appendChild(chip);
    });
    pendingImages.forEach((dataUrl, i) => {
        const thumb = document.createElement('div');
        thumb.className = 'attach-thumb';
        const img = document.createElement('img');
        img.src = dataUrl;
        img.alt = 'Pending attachment';
        const remove = document.createElement('button');
        remove.className = 'attach-remove';
        remove.innerHTML = '&times;';
        remove.title = 'Remove image';
        remove.onclick = () => removePendingImage(i);
        thumb.appendChild(img);
        thumb.appendChild(remove);
        attachPreviewEl.appendChild(thumb);
    });
}

function clearPendingImages() {
    pendingImages = [];
    pendingFiles = [];
    renderPendingImages();
    fileInputEl.value = '';
}

async function loadSessions() {
    const sessions = await api('GET', '/api/sessions');
    sessionListEl.innerHTML = '';
    for (const s of sessions) {
        const div = document.createElement('div');
        div.className = 'session-item' + (s.id === currentSessionId ? ' active' : '');
        div.dataset.id = s.id;
        div.onclick = () => switchSession(s.id);

        const row = document.createElement('div');
        row.className = 'session-item-main';

        const nameSpan = document.createElement('span');
        nameSpan.textContent = s.name || 'Untitled';
        nameSpan.className = 'session-name';
        nameSpan.onclick = () => switchSession(s.id);

        const pinBtn = document.createElement('button');
        pinBtn.className = 'pin-btn' + (s.is_pinned ? ' pinned' : '');
        pinBtn.innerHTML = ICONS.pin;
        pinBtn.title = s.is_pinned ? 'Unpin session' : 'Pin session';
        pinBtn.onclick = (e) => { e.stopPropagation(); togglePin(s.id, s.is_pinned, pinBtn); };

        const renameBtn = document.createElement('button');
        renameBtn.className = 'rename-btn';
        renameBtn.innerHTML = ICONS.rename;
        renameBtn.title = 'Rename session';
        renameBtn.onclick = (e) => {
            e.stopPropagation();
            startRename(s.id, nameSpan);
        };

        const exportBtn = document.createElement('button');
        exportBtn.className = 'export-btn';
        exportBtn.innerHTML = ICONS.export;
        exportBtn.title = 'Export session';
        exportBtn.onclick = (e) => { e.stopPropagation(); openExportDialog(s.id); };

        const delBtn = document.createElement('button');
        delBtn.className = 'delete-btn';
        delBtn.dataset.id = s.id;
        delBtn.innerHTML = ICONS.trash;
        delBtn.title = 'Delete session';
        delBtn.onclick = (e) => { e.stopPropagation(); deleteSession(s.id); };

        row.appendChild(nameSpan);
        row.appendChild(pinBtn);
        row.appendChild(renameBtn);
        row.appendChild(exportBtn);
        row.appendChild(delBtn);
        div.appendChild(row);

        if (s.summary) {
            const sum = document.createElement('div');
            sum.className = 'session-summary';
            sum.textContent = s.summary.length > 120 ? s.summary.slice(0, 120) + '…' : s.summary;
            sum.title = s.summary;
            div.appendChild(sum);
        }
        sessionListEl.appendChild(div);
    }
}

async function togglePin(id, currentlyPinned, btn) {
    try {
        await api('PATCH', `/api/sessions/${id}`, { pinned: !currentlyPinned });
        await loadSessions();
    } catch (e) {
        showError(e.message || 'Failed to update pin');
    }
}

function startRename(sessionId, nameSpan) {
    const current = nameSpan.textContent;
    const input = document.createElement('input');
    input.type = 'text';
    input.value = current;
    input.className = 'rename-input';
    input.style.cssText = 'background:var(--bg-primary);border:1px solid var(--accent);color:var(--text-primary);border-radius:4px;padding:2px 4px;font-size:13px;width:100%;outline:none;flex:1;min-width:0;';

    nameSpan.style.display = 'none';
    nameSpan.parentElement.insertBefore(input, nameSpan.nextSibling);
    input.focus();
    input.select();

    let done = false;
    const finish = async (save) => {
        if (done) return;
        done = true;
        const newName = input.value.trim();
        input.remove();
        nameSpan.style.display = '';
        if (save && newName && newName !== current) {
            nameSpan.textContent = newName;
            await api('PATCH', `/api/sessions/${sessionId}`, { name: newName });
        }
    };

    input.onkeydown = (e) => {
        if (e.key === 'Enter') { e.preventDefault(); finish(true); }
        if (e.key === 'Escape') finish(false);
    };
    input.onblur = () => finish(true);
}

async function createSession() {
    const res = await api('POST', '/api/sessions');
    currentSessionId = res.id;
    planDisplayEl.innerHTML = ''; // Clear plan display
    await api('POST', '/api/todos/clear'); // Clear todo tool state
    await loadSessions();
    showWelcome();
    connectWS();
}

async function switchSession(id) {
    if (isStreaming) return;
    currentSessionId = id;
    hideContinueButton();
    planDisplayEl.innerHTML = ''; // Clear plan display
    await api('POST', '/api/todos/clear'); // Clear todo tool state
    await loadSessions();
    await loadMessages();
    connectWS();
}

async function deleteSession(id) {
    await api('DELETE', `/api/sessions/${id}`);
    if (currentSessionId === id) {
        currentSessionId = null;
        messagesEl.innerHTML = '';
        showWelcome();
        if (ws) ws.close();
    }
    await loadSessions();
}

async function loadMessages() {
    const msgs = await api('GET', `/api/sessions/${currentSessionId}/messages`);
    messagesEl.innerHTML = '';
    currentToolPanel = null;
    toolCallCount = 0;
    currentContentEl = null;
    currentReasoningEl = null;
    textBuffer = '';
    if (msgs.length === 0) {
        showWelcome();
        return;
    }
    for (const m of msgs) {
        if (m.role === 'user') {
            finalizeToolPanel();
            appendUserMessage(m.content, m.attachments);
        } else if (m.role === 'assistant') {
            const hasContent = !!m.content;
            const hasTools = !!m.tool_calls;
            const hasReasoning = !!m.reasoning_content;
            if (hasTools) {
                if (!currentToolPanel) {
                    startAssistantMessage();
                    currentToolPanel.style.display = '';
                }
                if (hasReasoning) appendReasoningToCurrent(m.reasoning_content);
                if (hasContent) {
                    currentContentEl.innerHTML = marked.parse(m.content);
                }
                const tcs = typeof m.tool_calls === 'string' ? JSON.parse(m.tool_calls) : m.tool_calls;
                for (const tc of tcs) {
                    appendToolCall(tc.function?.name || tc.name, tc.function?.arguments || '{}', '');
                }
            } else if (hasContent) {
                finalizeToolPanel();
                appendAssistantMessage(m.content);
                if (hasReasoning) appendReasoningToCurrent(m.reasoning_content);
            } else if (hasReasoning) {
                // Pure reasoning turn (e.g. a reasoning model with no answer text).
                finalizeToolPanel();
                appendReasoningToCurrent(m.reasoning_content);
            }
        } else if (m.role === 'tool') {
            updateLastToolResult(m.content);
        }
    }
    finalizeToolPanel();
    scrollToBottom();
}

function showWelcome() {
    messagesEl.innerHTML = `
        <div class="welcome">
            <h2>CodeAssist</h2>
            <p>AI coding assistant connected to your workspace</p>
        </div>`;
}

function appendUserMessage(text, images = []) {
    removeWelcome();
    const div = document.createElement('div');
    div.className = 'message';
    div.innerHTML = `<div class="message-role user">You</div><div class="message-content user-content"></div>`;
    const content = div.querySelector('.message-content');
    if (images && images.length > 0) {
        const flex = document.createElement('div');
        flex.className = 'msg-image-row';
        for (const att of images) {
            const dataUrl = typeof att === 'string' ? att : att.data;
            if (typeof att === 'object' && att && att.attachment_type === 'text') {
                const chip = document.createElement('span');
                chip.className = 'msg-file-chip';
                chip.textContent = att.file_name || 'File';
                chip.title = 'Text file attached';
                flex.appendChild(chip);
            } else if (dataUrl) {
                const img = document.createElement('img');
                img.className = 'msg-image';
                img.src = dataUrl;
                img.alt = att.file_name || 'Image attachment';
                flex.appendChild(img);
            }
        }
        if (flex.childElementCount > 0) content.appendChild(flex);
    }
    content.appendChild(document.createTextNode(text));
    messagesEl.appendChild(div);
}

function appendAssistantMessage(text) {
    removeWelcome();
    const div = document.createElement('div');
    div.className = 'message';
    div.innerHTML =
        `<div class="message-role assistant">CodeAssist</div>` +
        `<div class="thinking-block" ${thinkingHiddenAttr()}><details><summary>${thinkingSummaryText()}</summary><div class="thinking-content"></div></details></div>` +
        `<div class="message-content"></div>`;
    messagesEl.appendChild(div);
    div.querySelector('.message-content').innerHTML = marked.parse(text);
    currentReasoningEl = div.querySelector('.thinking-content');
    applyThinkingVisibility(div.querySelector('.thinking-block'));
}

function startAssistantMessage() {
    removeWelcome();
    const div = document.createElement('div');
    div.className = 'message';
    div.innerHTML =
        `<div class="message-role assistant">CodeAssist</div>` +
        `<div class="thinking-block" ${thinkingHiddenAttr()}><details><summary>${thinkingSummaryText()}</summary><div class="thinking-content"></div></details></div>` +
        `<div class="tool-panel"></div><div class="message-content"></div>`;
    messagesEl.appendChild(div);
    currentContentEl = div.querySelector('.message-content');
    currentToolPanel = div.querySelector('.tool-panel');
    currentReasoningEl = div.querySelector('.thinking-content');
    toolCallCount = 0;
    const header = document.createElement('div');
    header.className = 'tool-panel-header';
    const panel = currentToolPanel;
    header.onclick = () => {
        header.classList.toggle('open');
        panel.querySelector('.tool-panel-body')?.classList.toggle('open');
    };
    currentToolPanel.appendChild(header);
    currentToolPanel.style.display = 'none';
    return currentContentEl;
}

// ── Thinking block (collapsible reasoning) ──────────────────────────────────
const THINKING_PREF_KEY = 'codeassist.thinkingVisible';

function thinkingVisibility() {
    return localStorage.getItem(THINKING_PREF_KEY) !== 'hidden';
}

function thinkingSummaryText() {
    return thinkingVisibility() ? '🤖 Thinking…' : '🤖 Thinking (hidden)';
}

function thinkingHiddenAttr() {
    return thinkingVisibility() ? '' : 'hidden';
}

function applyThinkingVisibility(wrapperEl) {
    // wrapperEl is the .thinking-block div; it owns the `hidden` attribute so
    // the initial template state and live toggles stay in sync.
    if (!wrapperEl) return;
    const summary = wrapperEl.querySelector('summary');
    if (thinkingVisibility()) {
        wrapperEl.removeAttribute('hidden');
        if (summary) summary.textContent = '🤖 Thinking…';
    } else {
        wrapperEl.setAttribute('hidden', '');
        if (summary) summary.textContent = '🤖 Thinking (hidden)';
    }
}

function toggleThinking() {
    localStorage.setItem(THINKING_PREF_KEY, thinkingVisibility() ? 'hidden' : 'shown');
    // Flip every thinking block to match the new preference.
    messagesEl.querySelectorAll('.thinking-block').forEach((w) => applyThinkingVisibility(w));
    const btn = document.getElementById('toggle-thinking-btn');
    if (btn) btn.textContent = thinkingVisibility() ? 'Hide thinking' : 'Show thinking';
}

function appendReasoningToCurrent(text) {
    if (!currentReasoningEl) startAssistantMessage();
    const p = document.createElement('p');
    p.textContent = text;
    currentReasoningEl.appendChild(p);
    applyThinkingVisibility(currentReasoningEl.closest('.message')?.querySelector('.thinking-block'));
}

function appendToolCall(name, args, output) {
    let argsStr = args;
    if (typeof args === 'object') {
        argsStr = JSON.stringify(args, null, 2);
    } else if (typeof args === 'string') {
        try { argsStr = JSON.stringify(JSON.parse(args), null, 2); } catch {}
    }

    // Ensure we have a message and panel
    if (!currentToolPanel) {
        startAssistantMessage();
    }

    // Show the panel on first tool call
    if (toolCallCount === 0) {
        currentToolPanel.style.display = '';
    }

    toolCallCount++;
    const header = currentToolPanel.querySelector('.tool-panel-header');
    header.textContent = `Tool calls (${toolCallCount})`;

    // Ensure body exists
    let body = currentToolPanel.querySelector('.tool-panel-body');
    if (!body) {
        body = document.createElement('div');
        body.className = 'tool-panel-body';
        currentToolPanel.appendChild(body);
    }

    const div = document.createElement('div');
    div.className = 'tool-call';
    div.innerHTML = `
        <div class="tool-call-header">${escapeHtml(name)}</div>
        <div class="tool-call-body">
            <div class="tool-call-args">${escapeHtml(argsStr)}</div>
            ${output ? `<div class="tool-result-label">Output</div><div class="tool-call-output">${highlightToolOutput(output)}</div>` : ''}
        </div>`;
    body.appendChild(div);

    div.querySelector('.tool-call-header').onclick = () => {
        div.querySelector('.tool-call-header').classList.toggle('open');
        div.querySelector('.tool-call-body').classList.toggle('open');
    };

    scrollToBottom();
}

function finalizeToolPanel() {
    // Don't null out — panel persists in the message DOM
    // Just reset the tracking variables so next turn creates fresh
    currentToolPanel = null;
    toolCallCount = 0;
}

function updateLastToolResult(output) {
    if (!currentToolPanel) return;
    const body = currentToolPanel.querySelector('.tool-panel-body');
    if (!body) return;
    const toolCalls = body.querySelectorAll('.tool-call');
    if (toolCalls.length === 0) return;
    const last = toolCalls[toolCalls.length - 1];
    const lastBody = last.querySelector('.tool-call-body');
    if (!lastBody.querySelector('.tool-call-output')) {
        const label = document.createElement('div');
        label.className = 'tool-result-label';
        label.textContent = 'Output';
        lastBody.appendChild(label);
        const outputDiv = document.createElement('div');
        outputDiv.className = 'tool-call-output' + (output && output.startsWith('Error') ? ' error' : '');
        outputDiv.innerHTML = highlightToolOutput(output);
        lastBody.appendChild(outputDiv);
    }
}

function removeWelcome() {
    const w = messagesEl.querySelector('.welcome');
    if (w) w.remove();
}

function scrollToBottom() {
    messagesEl.scrollTop = messagesEl.scrollHeight;
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function showQuestionDialog(questionId, data) {
    const div = document.createElement('div');
    div.className = 'question-dialog';
    div.id = `question-${questionId}`;

    const structured = data.questions && data.questions.length > 0;

    if (structured) {
        let questionsHtml = '';
        data.questions.forEach((q, idx) => {
            const isMultiple = q.multiple === true;
            const inputType = isMultiple ? 'checkbox' : (idx === 0 && data.questions.length === 1 && !q.options ? 'text' : (isMultiple ? 'checkbox' : 'radio'));

            let optionsHtml = '';
            if (q.options && q.options.length > 0) {
                optionsHtml = q.options.map((opt, oIdx) => `
                    <label class="question-option">
                        <input type="${inputType}" name="q${idx}" value="${escapeHtml(opt.label)}" data-idx="${idx}">
                        <span class="option-label">${escapeHtml(opt.label)}</span>
                        ${opt.description ? `<span class="option-desc">${escapeHtml(opt.description)}</span>` : ''}
                    </label>
                `).join('');
            }

            questionsHtml += `
                <div class="question-item">
                    ${q.header ? `<div class="question-header">${escapeHtml(q.header)}</div>` : ''}
                    <div class="question-text">${escapeHtml(q.question)}</div>
                    <div class="question-options">${optionsHtml}</div>
                    <input type="text" class="question-custom" name="q${idx}_custom" data-idx="${idx}" placeholder="Type your own answer...">
                </div>
            `;
        });

        div.innerHTML = `
            <div class="question-title">Agent has a question</div>
            ${questionsHtml}
            <div class="question-actions">
                <button class="question-btn submit">Submit</button>
                <button class="question-btn dismiss">Dismiss</button>
            </div>
        `;
    } else {
        const question = data.question || '';
        const options = data.options || [];
        let optionsHtml = '';

        if (options.length > 0) {
            optionsHtml = options.map((opt, oIdx) => `
                <label class="question-option">
                    <input type="radio" name="q0" value="${escapeHtml(opt)}">
                    <span class="option-label">${escapeHtml(opt)}</span>
                </label>
            `).join('');
        }

        div.innerHTML = `
            <div class="question-title">Agent has a question</div>
            <div class="question-item">
                <div class="question-text">${escapeHtml(question)}</div>
                <div class="question-options">${optionsHtml}</div>
                <input type="text" class="question-custom" name="q0_custom" data-idx="0" placeholder="Type your answer...">
            </div>
            <div class="question-actions">
                <button class="question-btn submit">Submit</button>
                <button class="question-btn dismiss">Dismiss</button>
            </div>
        `;
    }

    messagesEl.appendChild(div);
    scrollToBottom();

    const submitBtn = div.querySelector('.submit');
    const dismissBtn = div.querySelector('.dismiss');

    submitBtn.onclick = () => {
        let answers;
        if (structured) {
            answers = [];
            data.questions.forEach((q, idx) => {
                const customInput = div.querySelector(`[name="q${idx}_custom"]`);
                const selected = div.querySelectorAll(`input[name="q${idx}"]:checked`);
                if (selected.length > 0) {
                    const vals = Array.from(selected).map(s => s.value);
                    answers.push(q.multiple ? vals : vals[0]);
                } else if (customInput && customInput.value.trim()) {
                    answers.push(customInput.value.trim());
                } else {
                    answers.push("");
                }
            });
        } else {
            const customInput = div.querySelector('[name="q0_custom"]');
            const selected = div.querySelector('input[name="q0"]:checked');
            if (selected) {
                answers = selected.value;
            } else if (customInput && customInput.value.trim()) {
                answers = customInput.value.trim();
            } else {
                answers = "";
            }
        }

        div.remove();
        showProgress('Processing answer...');
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({
                type: 'question_response',
                id: questionId,
                answer: answers,
            }));
        }
    };

    dismissBtn.onclick = () => {
        div.remove();
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({
                type: 'question_rejected',
                id: questionId,
            }));
        }
    };
}

function showConfirmDialog(confirmId, toolName, args, inWorkspace) {
    const div = document.createElement('div');
    div.className = 'confirm-dialog';
    div.id = `confirm-${confirmId}`;

    let argsStr = args;
    if (typeof args === 'object') {
        argsStr = JSON.stringify(args, null, 2);
    }

    // Build trust options based on tool type
    const rememberHtml = `
        <label class="trust-option">
            <input type="checkbox" id="remember-${confirmId}">
            Always allow ${escapeHtml(toolName)} (remember permission)
        </label>
    `;
    const trustAllHtml = `
        <label class="trust-option">
            <input type="checkbox" id="trust-all-${confirmId}">
            Trust ALL tools for this session
        </label>
    `;
    let trustHtml = '';
    if (toolName === 'write' || toolName === 'edit') {
        trustHtml = `
            <label class="trust-option">
                <input type="checkbox" id="trust-workspace-${confirmId}">
                Trust all writes in workspace
            </label>
            ${trustAllHtml}
            ${rememberHtml}
        `;
    } else if (toolName === 'shell') {
        trustHtml = `
            <label class="trust-option">
                <input type="checkbox" id="trust-shell-${confirmId}">
                Trust all shell commands for this session
            </label>
            ${trustAllHtml}
            ${rememberHtml}
        `;
    } else {
        trustHtml = `
            <label class="trust-option">
                <input type="checkbox" id="trust-tool-${confirmId}">
                Trust this tool for this session
            </label>
            ${trustAllHtml}
            ${rememberHtml}
        `;
    }

    div.innerHTML = `
        <div class="confirm-title">Agent wants to use ${escapeHtml(toolName)}</div>
        <div class="confirm-details">${escapeHtml(argsStr)}</div>
        ${trustHtml ? `<div class="trust-options">${trustHtml}</div>` : ''}
        <div class="confirm-actions">
            <button class="confirm-btn approve">Allow</button>
            <button class="confirm-btn deny">Deny</button>
        </div>
    `;

    messagesEl.appendChild(div);
    scrollToBottom();

    div.querySelector('.approve').onclick = () => {
        const trustWorkspace = div.querySelector(`#trust-workspace-${confirmId}`)?.checked || false;
        const trustShell = div.querySelector(`#trust-shell-${confirmId}`)?.checked || false;
        const trustTool = div.querySelector(`#trust-tool-${confirmId}`)?.checked || false;
        const trustAll = div.querySelector(`#trust-all-${confirmId}`)?.checked || false;
        const remember = div.querySelector(`#remember-${confirmId}`)?.checked || false;
        div.remove();
        showProgress(`Executing ${toolName}...`);
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({
                type: 'confirm_response',
                id: confirmId,
                approved: true,
                trust_workspace: trustWorkspace,
                trust_shell: trustShell,
                trust_tool: trustTool,
                trust_all: trustAll,
                remember: remember,
            }));
        }
    };

    div.querySelector('.deny').onclick = () => {
        div.remove();
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({
                type: 'confirm_response',
                id: confirmId,
                approved: false,
            }));
        }
    };
}

// ── Session export / import ─────────────────────────────────────────────────

function openModal(bodyHtml, actionsHtml) {
    const backdrop = document.createElement('div');
    backdrop.className = 'modal-backdrop';
    const card = document.createElement('div');
    card.className = 'modal-card';
    card.innerHTML = `
        <div class="modal-body">${bodyHtml}</div>
        <div class="modal-actions">${actionsHtml}</div>
    `;
    backdrop.appendChild(card);
    document.body.appendChild(backdrop);

    const close = () => {
        backdrop.remove();
        if (typeof backdrop._onClose === 'function') backdrop._onClose();
    };
    backdrop.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') close();
    });
    backdrop.onclick = (e) => { if (e.target === backdrop) close(); };
    card.querySelector('.modal-close')?.addEventListener('click', close);
    return backdrop;
}

async function downloadJSON(filename, data) {
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
}

async function openExportDialog(sessionId) {
    const bd = openModal(
        `<p class="modal-hint">Download this session as a JSON file you can later <strong>Import</strong>.</p>
         <label class="trust-option modal-check">
             <input type="checkbox" id="export-redact"> Redact PII (API keys, secrets, images)
         </label>`,
        `<button class="modal-btn" id="export-cancel">Cancel</button>
         <button class="modal-btn primary" id="export-download">Download JSON</button>`
    );
    bd.querySelector('#export-cancel').addEventListener('click', () => bd.remove());
    bd.querySelector('#export-download').addEventListener('click', async () => {
        const btn = bd.querySelector('#export-download');
        const redact = bd.querySelector('#export-redact').checked;
        btn.disabled = true;
        btn.textContent = 'Exporting…';
        try {
            const data = await api('POST', '/api/sessions/export', { session_id: sessionId, redact });
            const base = (data.name || 'session').replace(/[^\w\-]+/g, '_');
            await downloadJSON(`${base}${redact ? '-redacted' : ''}.json`, data);
            showStatus(`Exported session${redact ? ' (PII redacted)' : ''}.`, 'success');
        } catch (e) {
            showError(e.message || 'Failed to export session');
            btn.disabled = false;
            btn.textContent = 'Download JSON';
        }
    });
}

async function openImportDialog() {
    const bd = openModal(
        `<p class="modal-hint">Import a session from a JSON file or pasted JSON. A new session is created; your current chat is untouched.</p>
         <label class="modal-label">Session name (optional)</label>
         <input type="text" id="import-name" class="modal-input" placeholder="Imported session" />
         <label class="modal-label">Choose a .json file</label>
         <input type="file" id="import-file" class="modal-file" accept=".json,application/json" />
         <label class="modal-label">— or paste JSON here</label>
         <textarea id="import-paste" class="modal-textarea" placeholder='{"version": 2, "messages": [...]}'></textarea>
         <p class="modal-hint" id="import-file-name"></p>`,
        `<button class="modal-btn" id="import-cancel">Cancel</button>
         <button class="modal-btn primary" id="import-run">Import</button>`
    );
    const fileInput = bd.querySelector('#import-file');
    const nameField = bd.querySelector('#import-name');
    const pasteArea = bd.querySelector('#import-paste');
    const fileNameLabel = bd.querySelector('#import-file-name');

    fileInput.addEventListener('change', () => {
        fileNameLabel.textContent = fileInput.files[0] ? `Selected: ${fileInput.files[0].name}` : '';
        // Pre-fill the paste area with the chosen file's contents.
        const reader = new FileReader();
        reader.onload = () => { pasteArea.value = String(reader.result || ''); };
        reader.readAsText(fileInput.files[0]);
    });

    bd.querySelector('#import-cancel').addEventListener('click', () => bd.remove());
    bd.querySelector('#import-run').addEventListener('click', async () => {
        const btn = bd.querySelector('#import-run');
        let raw = pasteArea.value.trim();
        if (!raw) {
            showError('Choose a .json file or paste JSON before importing.');
            return;
        }
        let exportData;
        try {
            exportData = JSON.parse(raw);
        } catch (e) {
            showError(`Import failed: invalid JSON (${e.message})`);
            return;
        }
        btn.disabled = true;
        btn.textContent = 'Importing…';
        try {
            const name = nameField.value.trim() || undefined;
            const res = await api('POST', '/api/sessions/import', { data: exportData, name });
            bd.remove();
            await switchSession(res.id);
            showStatus('Session imported.', 'success');
        } catch (e) {
            showError(e.message || 'Failed to import session');
            btn.disabled = false;
            btn.textContent = 'Import';
        }
    });
}

function connectWS() {
    if (ws) {
        ws.onclose = null;
        ws.close();
    }
    clearTimeout(reconnectTimer);
    if (!currentSessionId) return;

    wsConnected = false;
    updateConnectionStatus('connecting');

    ws = new WebSocket(`${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/ws/${currentSessionId}`);

    ws.onopen = () => {
        wsConnected = true;
        updateConnectionStatus('connected');
        // (Re)arm the keepalive; clear any stale timer first.
        if (pingTimer) clearInterval(pingTimer);
        pingTimer = setInterval(() => {
            if (ws && ws.readyState === WebSocket.OPEN) {
                try { ws.send(JSON.stringify({ type: 'ping' })); } catch (_) { /* ignored by server */ }
            }
        }, PING_INTERVAL_MS);
    };

    ws.onmessage = (event) => {
        const data = JSON.parse(event.data);

        if (data.type === 'active_agent') {
            if (data.agent) setAgentSelection(data.agent.id || data.agent.name);
        } else if (data.type === 'agent_switched') {
            setAgentSelection((data.agent && (data.agent.id || data.agent.name)) || data.agent);
        } else if (data.type === 'text_delta') {
            hideProgress();
            if (!currentContentEl) startAssistantMessage();
            textBuffer += data.content;
            currentContentEl.innerHTML = marked.parse(textBuffer);
            scrollToBottom();
        } else if (data.type === 'tool_call') {
            hideProgress();
            appendToolCall(data.name, data.arguments, '');
            showProgress(`Executing ${data.name}...`);
        } else if (data.type === 'tool_result') {
            hideProgress();
            updateLastToolResult(data.output);
            scrollToBottom();
        } else if (data.type === 'context') {
            updateContextUsage(data.tokens, data.usage_pct, data.severity);
        } else if (data.type === 'compacted') {
            showError(data.message);
        } else if (data.type === 'plan_update') {
            updatePlanDisplay(data.tasks);
        } else if (data.type === 'question_request') {
            hideProgress();
            showQuestionDialog(data.id, data);
        } else if (data.type === 'confirm_request') {
            hideProgress();
            showConfirmDialog(data.id, data.tool, data.arguments, data.in_workspace);
        } else if (data.type === 'error') {
            hideProgress();
            if (!currentContentEl) startAssistantMessage();
            currentContentEl.innerHTML += `<p style="color:var(--red);margin-top:8px;">Error: ${escapeHtml(data.message)}</p>`;
            scrollToBottom();
            isStreaming = false;
            sendBtn.disabled = false;
            sendBtn.style.display = 'flex';
            stopBtn.style.display = 'none';
            inputEl.disabled = false;
            setAttachmentUiBusy(false);
            inputEl.focus();
        } else if (data.type === 'incomplete') {
            hideProgress();
            finalizeToolPanel();
            if (!currentContentEl) startAssistantMessage();
            currentContentEl.innerHTML += `<p style="color:var(--yellow);margin-top:8px;">⚠ ${escapeHtml(data.message)}</p>`;
            scrollToBottom();
            showContinueButton();

            currentContentEl = null;
            currentReasoningEl = null;
            textBuffer = '';
            isStreaming = false;
            sendBtn.disabled = false;
            sendBtn.style.display = 'flex';
            stopBtn.style.display = 'none';
            inputEl.disabled = false;
            setAttachmentUiBusy(false);
            inputEl.focus();
        } else if (data.type === 'done') {
            hideProgress();
            finalizeToolPanel();
            if (textBuffer || messagesEl.querySelectorAll('.tool-panel').length > 0) {
                showContinueButton();
            }
            // Show clear "done" indicator
            const doneDiv = document.createElement('div');
            doneDiv.className = 'message-actions';
            doneDiv.innerHTML = `<span style="color:var(--green);font-size:12px;">&#10003; Complete</span>`;
            messagesEl.appendChild(doneDiv);
            scrollToBottom();

            currentContentEl = null;
            currentReasoningEl = null;
            textBuffer = '';
            isStreaming = false;
            sendBtn.disabled = false;
            sendBtn.style.display = 'flex';
            stopBtn.style.display = 'none';
            inputEl.disabled = false;
            setAttachmentUiBusy(false);
            inputEl.focus();
        } else if (data.type === 'cancelled') {
            hideProgress();
            if (!currentContentEl) currentContentEl = startAssistantMessage();
            currentContentEl.innerHTML += `<p style="color:var(--yellow);margin-top:8px;font-style:italic;">Stopped by user</p>`;
            scrollToBottom();
        } else if (data.type === 'reasoning') {
            hideProgress();
            if (!currentReasoningEl) {
                startAssistantMessage();
            }
            const p = document.createElement('p');
            p.textContent = data.content;
            currentReasoningEl.appendChild(p);
            applyThinkingVisibility(currentReasoningEl.closest('.message')?.querySelector('.thinking-block'));
            scrollToBottom();
        } else if (data.type === 'finish') {
            const usage = data.usage;
            if (usage) {
                const turn = (usage.prompt_tokens || 0) + (usage.completion_tokens || 0);
                const now = performance.now();
                if (tokenState.lastTime != null) {
                    const dtSec = (now - tokenState.lastTime) / 1000;
                    tokenState.rate = dtSec > 0 ? turn / dtSec : 0;
                } else {
                    tokenState.rate = 0;
                }
                tokenState.total += turn;
                tokenState.lastTime = now;
                updateTokenInfo(tokenState.total, tokenState.rate);
            }
        }
    };

    ws.onclose = (event) => {
        wsConnected = false;
        if (pingTimer) { clearInterval(pingTimer); pingTimer = null; }
        updateConnectionStatus('disconnected');
        if (!isStreaming) {
            sendBtn.disabled = false;
            sendBtn.style.display = 'flex';
            stopBtn.style.display = 'none';
            inputEl.disabled = false;
        }
        // Don't reconnect on4001 (auth failure) or intentional close
        if (event.code === 4001) {
            updateConnectionStatus('error');
            return;
        }
        // Faster reconnect on initial load
        const delay = document.querySelector('.welcome') ? 1000 : 3000;
        reconnectTimer = setTimeout(() => {
            if (currentSessionId && !isStreaming) connectWS();
        }, delay);
    };

    ws.onerror = () => {
        updateConnectionStatus('error');
    };
}

function updateConnectionStatus(status) {
    let el = document.getElementById('connection-status');
    if (!el) {
        el = document.createElement('div');
        el.id = 'connection-status';
        document.querySelector('.sidebar-footer').prepend(el);
    }
    const labels = {
        connected: '',
        connecting: 'Connecting...',
        disconnected: 'Disconnected - reconnecting...',
        error: 'Connection error',
    };
    el.textContent = labels[status] || '';
    el.style.display = labels[status] ? 'block' : 'none';
    el.style.color = status === 'connected' ? 'var(--green)' : status === 'error' ? 'var(--red)' : 'var(--yellow)';
    el.style.fontSize = '11px';
    el.style.marginBottom = '4px';
}

function sendMessage() {
    const text = inputEl.value.trim();
    const hasImages = pendingImages.length > 0;
    const hasFiles = pendingFiles.length > 0;
    if ((!text && !hasImages && !hasFiles) || isStreaming) return;

    if (!ws || ws.readyState !== WebSocket.OPEN) {
        showError('Not connected to server. Reconnecting...');
        connectWS();
        // Queue the message to send after connection
        const checkAndSend = setInterval(() => {
            if (ws && ws.readyState === WebSocket.OPEN) {
                clearInterval(checkAndSend);
                hideError();
                sendMessage();
            }
        }, 500);
        // Stop trying after 5 seconds
        setTimeout(() => clearInterval(checkAndSend), 5000);
        return;
    }

    isStreaming = true;
    sendBtn.disabled = true;
    sendBtn.style.display = 'none';
    stopBtn.style.display = 'flex';
    inputEl.disabled = true;
    setAttachmentUiBusy(true);
    inputEl.value = '';
    inputEl.style.height = 'auto';

    const images = pendingImages.slice();
    const files = pendingFiles.slice().map(f => ({ name: f.name, content: f.content }));
    appendUserMessage(text, [...files.map(f => ({ attachment_type: 'text', file_name: f.name })), ...images]);
    clearPendingImages();
    hideContinueButton();
    showProgress('Thinking...');
    ws.send(JSON.stringify({ type: 'user_message', content: text, images, files }));
}

function showError(msg) {
    removeWelcome();
    hideProgress();
    const div = document.createElement('div');
    div.className = 'message system-error';
    div.innerHTML = `<div class="message-role" style="color:var(--red)">System</div><div class="message-content"><p style="color:var(--red)">${escapeHtml(msg)}</p></div>`;
    messagesEl.appendChild(div);
    scrollToBottom();
}

function hideError() {
    const errors = messagesEl.querySelectorAll('.system-error');
    if (errors.length > 0) {
        errors[errors.length - 1].remove();
    }
}

let statusTimers = [];
function showStatus(msg, kind = 'info') {
    removeWelcome();
    hideProgress();
    const div = document.createElement('div');
    div.className = `message system-status system-${kind}`;
    const color = kind === 'success' ? 'var(--green)' : kind === 'error' ? 'var(--red)' : 'var(--text-secondary)';
    div.innerHTML = `<div class="message-role" style="color:${color}">Status</div><div class="message-content"><p style="color:${color}">${escapeHtml(msg)}</p></div>`;
    messagesEl.appendChild(div);
    scrollToBottom();
    const timer = setTimeout(() => { div.classList.add('fading'); setTimeout(() => div.remove(), 300); }, 4000);
    statusTimers.push(timer);
}

let progressEl = null;
let progressShowTime = 0;
let progressMinTimer = null;
let continueBtnContainer = null;

function showProgress(status) {
    // Always remove any existing progress bar immediately
    clearTimeout(progressMinTimer);
    if (progressEl) progressEl.remove();
    progressEl = null;

    hideContinueButton();
    progressEl = document.createElement('div');
    progressEl.className = 'progress-bar';
    progressEl.innerHTML = `<div class="spinner"></div><div class="status-text">${escapeHtml(status)}</div>`;
    messagesEl.appendChild(progressEl);
    scrollToBottom();
    progressShowTime = Date.now();
}

function updateProgress(status) {
    if (progressEl) {
        const textEl = progressEl.querySelector('.status-text');
        if (textEl) textEl.textContent = status;
    }
}

function hideProgress() {
    if (!progressEl) return;
    clearTimeout(progressMinTimer);
    progressEl.remove();
    progressEl = null;
}

function showContinueButton() {
    hideContinueButton();
    continueBtnContainer = document.createElement('div');
    continueBtnContainer.className = 'message-actions';
    continueBtnContainer.innerHTML = `<button id="continue-btn">Continue</button>`;
    messagesEl.appendChild(continueBtnContainer);
    continueBtnContainer.querySelector('#continue-btn').onclick = () => {
        hideContinueButton();
        inputEl.value = 'Continue with the original task — take the actions needed to complete it (make the code/documentation changes, don\'t just summarize or stop after research).';
        sendMessage();
    };
    scrollToBottom();
}

function hideContinueButton() {
    if (continueBtnContainer) {
        continueBtnContainer.remove();
        continueBtnContainer = null;
    }
}

function updateContextUsage(tokens, usagePct, severity) {
    let el = document.getElementById('context-usage');
    if (!el) {
        el = document.createElement('div');
        el.id = 'context-usage';
        document.querySelector('.sidebar-footer').appendChild(el);
    }
    const color = severity === 'critical' ? 'var(--red)' : severity === 'warning' ? 'var(--yellow)' : 'var(--text-muted)';
    el.innerHTML = `<span style="color:${color}">${usagePct}% context used</span> (${tokens.toLocaleString()} tokens)`;
    el.style.fontSize = '11px';
    el.style.marginTop = '4px';
}

function updateTokenInfo(totalTokens, rate) {
    let el = document.getElementById('token-info');
    if (!el) {
        el = document.createElement('div');
        el.id = 'token-info';
        el.style.fontSize = '11px';
        el.style.color = 'var(--text-muted)';
        el.style.marginTop = '2px';
        document.querySelector('.sidebar-footer').appendChild(el);
    }
    const rateText = rate > 0 ? ` · ${rate.toFixed(1)} tok/s` : '';
    el.textContent = `${totalTokens.toLocaleString()} tokens${rateText}`;
}

function updatePlanDisplay(tasks) {
    if (!tasks || tasks.length === 0) {
        planDisplayEl.innerHTML = '';
        return;
    }

    const container = document.createElement('div');
    container.className = 'plan-container';

    const title = document.createElement('div');
    title.className = 'plan-title';
    title.textContent = 'Current Plan';
    container.appendChild(title);

    for (const task of tasks) {
        const item = document.createElement('div');
        item.className = `plan-item ${task.status}`;

        const checkbox = document.createElement('div');
        checkbox.className = 'plan-checkbox';
        if (task.status === 'completed') {
            checkbox.innerHTML = '&#10003;';
        } else if (task.status === 'in_progress') {
            checkbox.innerHTML = '&#8987;';
        }

        const content = document.createElement('span');
        content.textContent = task.content;

        item.appendChild(checkbox);
        item.appendChild(content);
        container.appendChild(item);
    }

    planDisplayEl.innerHTML = '';
    planDisplayEl.appendChild(container);
}

async function loadTodos() {
    try {
        const data = await api('GET', '/api/todos');
        if (data.tasks && data.tasks.length > 0) {
            updatePlanDisplay(data.tasks);
        }
    } catch (e) {
        // Ignore errors loading todos
    }
}

inputEl.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
    }
});

inputEl.addEventListener('input', () => {
    inputEl.style.height = 'auto';
    inputEl.style.height = Math.min(inputEl.scrollHeight, 200) + 'px';
});

sendBtn.addEventListener('click', sendMessage);

attachBtn.addEventListener('click', () => fileInputEl.click());
fileInputEl.addEventListener('change', async () => {
    for (const file of Array.from(fileInputEl.files)) {
        if (file.type.startsWith('image/')) {
            await addPendingImage(file);
        } else if (isTextFile(file)) {
            await addPendingFile(file);
        } else {
            showError(`Unsupported file type '${file.type || 'unknown'}' for '${file.name}'. Only images and text files are supported.`);
        }
    }
    fileInputEl.value = '';
});
inputEl.addEventListener('paste', (e) => {
    const items = e.clipboardData?.items || [];
    for (const item of items) {
        if (item.type.startsWith('image/')) {
            const file = item.getAsFile();
            if (file) addPendingImage(file);
        }
    }
});
inputAreaEl.addEventListener('dragover', (e) => {
    if (isStreaming) return;
    e.preventDefault();
    inputAreaEl.classList.add('drag-over');
});
inputAreaEl.addEventListener('dragleave', () => {
    inputAreaEl.classList.remove('drag-over');
});
inputAreaEl.addEventListener('drop', (e) => {
    if (isStreaming) return;
    e.preventDefault();
    inputAreaEl.classList.remove('drag-over');
    for (const file of Array.from(e.dataTransfer.files || [])) {
        if (file.type.startsWith('image/')) {
            addPendingImage(file);
        } else if (isTextFile(file)) {
            addPendingFile(file);
        } else {
            showError(`Unsupported file type '${file.type || 'unknown'}' for '${file.name}'. Only images and text files are supported.`);
        }
    }
});

stopBtn.addEventListener('click', () => {
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'cancel' }));
    }
});
newSessionBtn.addEventListener('click', createSession);

const toggleThinkingBtn = document.getElementById('toggle-thinking-btn');
if (toggleThinkingBtn) {
    toggleThinkingBtn.addEventListener('click', toggleThinking);
    toggleThinkingBtn.textContent = thinkingVisibility() ? 'Hide thinking' : 'Show thinking';
}

const importBtn = document.getElementById('import-btn');
if (importBtn) {
    importBtn.innerHTML = ICONS.import;
    importBtn.addEventListener('click', openImportDialog);
}

// --- Sidebar show/hide toggle ---------------------------------------------
const SIDEBAR_HIDDEN_KEY = 'codeassist:sidebarVisible';

function applySidebarHidden(hidden) {
    const app = document.getElementById('app');
    if (!app) return;
    app.classList.toggle('sidebar-hidden', !!hidden);
    const reveal = document.getElementById('sidebar-reveal');
    if (reveal) reveal.title = hidden ? 'Show sidebar' : 'Collapse sidebar';
}

function initSidebarToggle() {
    const app = document.getElementById('app');
    if (!app) return;
    const stored = localStorage.getItem(SIDEBAR_HIDDEN_KEY);
    applySidebarHidden(stored === 'false');

    const collapse = document.getElementById('sidebar-collapse');
    if (collapse) {
        collapse.addEventListener('click', () => {
            const hidden = !app.classList.contains('sidebar-hidden');
            applySidebarHidden(hidden);
            localStorage.setItem(SIDEBAR_HIDDEN_KEY, String(hidden));
        });
    }
    const reveal = document.getElementById('sidebar-reveal');
    if (reveal) {
        reveal.addEventListener('click', () => {
            applySidebarHidden(false);
            localStorage.setItem(SIDEBAR_HIDDEN_KEY, 'true');
        });
    }
    window.addEventListener('keydown', (e) => {
        if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'b') {
            e.preventDefault();
            const hidden = !app.classList.contains('sidebar-hidden');
            applySidebarHidden(hidden);
            localStorage.setItem(SIDEBAR_HIDDEN_KEY, String(hidden));
        }
    });
}

initSidebarToggle();

(async () => {
    await loadConfig();
    let sessions = await api('GET', '/api/sessions');
    if (sessions.length === 0) {
        // Auto-create first session
        const res = await api('POST', '/api/sessions');
        currentSessionId = res.id;
        sessions = await api('GET', '/api/sessions');
        showWelcome();
        connectWS();
    } else {
        currentSessionId = sessions[0].id;
        await loadMessages();
        await loadTodos();
        connectWS();
    }
    await loadSessions();
})();
