const messagesEl = document.getElementById('messages');
const inputEl = document.getElementById('user-input');
const sendBtn = document.getElementById('send-btn');
const sessionListEl = document.getElementById('session-list');
const newSessionBtn = document.getElementById('new-session-btn');
const modelInfoEl = document.getElementById('model-info');

let currentSessionId = null;
let ws = null;
let wsConnected = false;
let isStreaming = false;
let currentContentEl = null;
let textBuffer = '';
let reconnectTimer = null;

marked.setOptions({
    highlight: (code, lang) => {
        if (lang && hljs.getLanguage(lang)) {
            return hljs.highlight(code, { language: lang }).value;
        }
        return hljs.highlightAuto(code).value;
    },
    breaks: true,
});

async function api(method, path, body) {
    const opts = { method, headers: { 'Content-Type': 'application/json' } };
    if (body) opts.body = JSON.stringify(body);
    const res = await fetch(path, opts);
    return res.json();
}

async function loadConfig() {
    const cfg = await api('GET', '/api/config');
    modelInfoEl.textContent = `${cfg.model} | ${cfg.workspace}`;
}

async function loadSessions() {
    const sessions = await api('GET', '/api/sessions');
    sessionListEl.innerHTML = '';
    for (const s of sessions) {
        const div = document.createElement('div');
        div.className = 'session-item' + (s.id === currentSessionId ? ' active' : '');
        div.dataset.id = s.id;

        const nameSpan = document.createElement('span');
        nameSpan.textContent = s.name || 'Untitled';
        nameSpan.className = 'session-name';
        nameSpan.onclick = () => switchSession(s.id);

        const renameBtn = document.createElement('button');
        renameBtn.className = 'rename-btn';
        renameBtn.innerHTML = '&#9998;';
        renameBtn.title = 'Rename session';
        renameBtn.onclick = (e) => {
            e.stopPropagation();
            startRename(div, s.id, nameSpan);
        };

        const delBtn = document.createElement('button');
        delBtn.className = 'delete-btn';
        delBtn.dataset.id = s.id;
        delBtn.innerHTML = '&times;';
        delBtn.title = 'Delete session';
        delBtn.onclick = (e) => { e.stopPropagation(); deleteSession(s.id); };

        div.appendChild(nameSpan);
        div.appendChild(renameBtn);
        div.appendChild(delBtn);
        sessionListEl.appendChild(div);
    }
}

function startRename(container, sessionId, nameSpan) {
    const current = nameSpan.textContent;
    const input = document.createElement('input');
    input.type = 'text';
    input.value = current;
    input.className = 'rename-input';
    input.style.cssText = 'background:var(--bg-primary);border:1px solid var(--accent);color:var(--text-primary);border-radius:4px;padding:2px 4px;font-size:13px;width:100%;outline:none;flex:1;min-width:0;';

    nameSpan.style.display = 'none';
    container.insertBefore(input, nameSpan.nextSibling);
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
    await loadSessions();
    showWelcome();
    connectWS();
}

async function switchSession(id) {
    if (isStreaming) return;
    currentSessionId = id;
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
    if (msgs.length === 0) {
        showWelcome();
        return;
    }
    for (const m of msgs) {
        if (m.role === 'user') {
            appendUserMessage(m.content);
        } else if (m.role === 'assistant') {
            if (m.content) appendAssistantMessage(m.content);
            if (m.tool_calls) {
                const tcs = typeof m.tool_calls === 'string' ? JSON.parse(m.tool_calls) : m.tool_calls;
                for (const tc of tcs) {
                    appendToolCall(tc.function?.name || tc.name, tc.function?.arguments || '{}', '');
                }
            }
        } else if (m.role === 'tool') {
            updateLastToolResult(m.content);
        }
    }
    scrollToBottom();
}

function showWelcome() {
    messagesEl.innerHTML = `
        <div class="welcome">
            <h2>CodeAssist</h2>
            <p>AI coding assistant connected to your workspace</p>
            <p class="welcome-hint">Hover a session name to rename or delete it</p>
        </div>`;
}

function appendUserMessage(text) {
    removeWelcome();
    const div = document.createElement('div');
    div.className = 'message';
    div.innerHTML = `<div class="message-role user">You</div><div class="message-content">${escapeHtml(text)}</div>`;
    messagesEl.appendChild(div);
}

function appendAssistantMessage(text) {
    removeWelcome();
    const div = document.createElement('div');
    div.className = 'message';
    div.innerHTML = `<div class="message-role assistant">CodeAssist</div><div class="message-content"></div>`;
    messagesEl.appendChild(div);
    div.querySelector('.message-content').innerHTML = marked.parse(text);
}

function startAssistantMessage() {
    removeWelcome();
    const div = document.createElement('div');
    div.className = 'message';
    div.innerHTML = `<div class="message-role assistant">CodeAssist</div><div class="message-content"></div>`;
    messagesEl.appendChild(div);
    currentContentEl = div.querySelector('.message-content');
    return currentContentEl;
}

function appendToolCall(name, args, output) {
    let argsStr = args;
    if (typeof args === 'object') {
        argsStr = JSON.stringify(args, null, 2);
    } else if (typeof args === 'string') {
        try { argsStr = JSON.stringify(JSON.parse(args), null, 2); } catch {}
    }

    const div = document.createElement('div');
    div.className = 'tool-call';
    div.innerHTML = `
        <div class="tool-call-header">${escapeHtml(name)}</div>
        <div class="tool-call-body">
            <div class="tool-call-args">${escapeHtml(argsStr)}</div>
            ${output ? `<div class="tool-result-label">Output</div><div class="tool-call-output">${escapeHtml(output)}</div>` : ''}
        </div>`;
    messagesEl.appendChild(div);

    div.querySelector('.tool-call-header').onclick = () => {
        div.querySelector('.tool-call-header').classList.toggle('open');
        div.querySelector('.tool-call-body').classList.toggle('open');
    };

    if (!output) {
        div.querySelector('.tool-call-header').classList.add('open');
        div.querySelector('.tool-call-body').classList.add('open');
    }

    scrollToBottom();
}

function updateLastToolResult(output) {
    const toolCalls = messagesEl.querySelectorAll('.tool-call');
    if (toolCalls.length === 0) return;
    const last = toolCalls[toolCalls.length - 1];
    const body = last.querySelector('.tool-call-body');
    if (!body.querySelector('.tool-call-output')) {
        const label = document.createElement('div');
        label.className = 'tool-result-label';
        label.textContent = 'Output';
        body.appendChild(label);
        const outputDiv = document.createElement('div');
        outputDiv.className = 'tool-call-output' + (output && output.startsWith('Error') ? ' error' : '');
        outputDiv.textContent = output;
        body.appendChild(outputDiv);
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

function connectWS() {
    if (ws) ws.close();
    clearTimeout(reconnectTimer);
    if (!currentSessionId) return;

    wsConnected = false;
    updateConnectionStatus('connecting');

    ws = new WebSocket(`ws://${location.host}/ws/${currentSessionId}`);

    ws.onopen = () => {
        wsConnected = true;
        updateConnectionStatus('connected');
    };

    ws.onmessage = (event) => {
        const data = JSON.parse(event.data);

        if (data.type === 'text_delta') {
            hideProgress();
            if (!currentContentEl) currentContentEl = startAssistantMessage();
            textBuffer += data.content;
            currentContentEl.innerHTML = marked.parse(textBuffer);
            scrollToBottom();
        } else if (data.type === 'tool_call') {
            hideProgress();
            appendToolCall(data.name, data.arguments, '');
            updateProgress(`Executing ${data.name}...`);
        } else if (data.type === 'tool_result') {
            updateLastToolResult(data.output);
            scrollToBottom();
        } else if (data.type === 'error') {
            hideProgress();
            if (!currentContentEl) currentContentEl = startAssistantMessage();
            currentContentEl.innerHTML += `<p style="color:var(--red);margin-top:8px;">Error: ${escapeHtml(data.message)}</p>`;
            scrollToBottom();
        } else if (data.type === 'done') {
            hideProgress();
            currentContentEl = null;
            textBuffer = '';
            isStreaming = false;
            sendBtn.disabled = false;
            inputEl.disabled = false;
            inputEl.focus();
        } else if (data.type === 'finish') {
            // usage info
        }
    };

    ws.onclose = () => {
        wsConnected = false;
        updateConnectionStatus('disconnected');
        if (!isStreaming) {
            sendBtn.disabled = false;
            inputEl.disabled = false;
        }
        reconnectTimer = setTimeout(() => {
            if (currentSessionId && !isStreaming) connectWS();
        }, 3000);
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
    if (!text || isStreaming) return;

    if (!ws || ws.readyState !== WebSocket.OPEN) {
        showError('Not connected to server. Reconnecting...');
        connectWS();
        return;
    }

    isStreaming = true;
    sendBtn.disabled = true;
    inputEl.disabled = true;
    inputEl.value = '';
    inputEl.style.height = 'auto';

    appendUserMessage(text);
    showProgress('Thinking...');
    ws.send(JSON.stringify({ type: 'user_message', content: text }));
}

function showError(msg) {
    removeWelcome();
    hideProgress();
    const div = document.createElement('div');
    div.className = 'message';
    div.innerHTML = `<div class="message-role" style="color:var(--red)">System</div><div class="message-content"><p style="color:var(--red)">${escapeHtml(msg)}</p></div>`;
    messagesEl.appendChild(div);
    scrollToBottom();
}

let progressEl = null;

function showProgress(status) {
    hideProgress();
    progressEl = document.createElement('div');
    progressEl.className = 'progress-bar';
    progressEl.innerHTML = `<div class="spinner"></div><div class="status-text">${escapeHtml(status)}</div>`;
    messagesEl.appendChild(progressEl);
    scrollToBottom();
}

function updateProgress(status) {
    if (progressEl) {
        const textEl = progressEl.querySelector('.status-text');
        if (textEl) textEl.textContent = status;
    }
}

function hideProgress() {
    if (progressEl) {
        progressEl.remove();
        progressEl = null;
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
newSessionBtn.addEventListener('click', createSession);

(async () => {
    await loadConfig();
    const sessions = await api('GET', '/api/sessions');
    if (sessions.length > 0) {
        currentSessionId = sessions[0].id;
        await loadMessages();
        connectWS();
    } else {
        showWelcome();
    }
    await loadSessions();
})();
