const messagesEl = document.getElementById('messages');
const inputEl = document.getElementById('user-input');
const sendBtn = document.getElementById('send-btn');
const sessionListEl = document.getElementById('session-list');
const newSessionBtn = document.getElementById('new-session-btn');
const modelInfoEl = document.getElementById('model-info');

let currentSessionId = null;
let ws = null;
let isStreaming = false;
let currentAssistantEl = null;
let currentContentEl = null;

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
        div.innerHTML = `<span>${s.name || 'Untitled'}</span><button class="delete-btn" data-id="${s.id}">&times;</button>`;
        div.querySelector('span').onclick = () => switchSession(s.id);
        div.querySelector('.delete-btn').onclick = (e) => { e.stopPropagation(); deleteSession(s.id); };
        sessionListEl.appendChild(div);
    }
}

async function createSession() {
    const res = await api('POST', '/api/sessions');
    currentSessionId = res.id;
    await loadSessions();
    showWelcome();
    connectWS();
}

async function switchSession(id) {
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
    const contentEl = div.querySelector('.message-content');
    contentEl.innerHTML = marked.parse(text);
    return contentEl;
}

function startAssistantMessage() {
    removeWelcome();
    const div = document.createElement('div');
    div.className = 'message';
    div.innerHTML = `<div class="message-role assistant">CodeAssist</div><div class="message-content"></div>`;
    messagesEl.appendChild(div);
    currentAssistantEl = div;
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
        const header = div.querySelector('.tool-call-header');
        const body = div.querySelector('.tool-call-body');
        header.classList.toggle('open');
        body.classList.toggle('open');
    };

    if (!output) {
        div.querySelector('.tool-call-header').classList.add('open');
        div.querySelector('.tool-call-body').classList.add('open');
    }

    scrollToBottom();
    return div;
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
    if (!currentSessionId) return;

    ws = new WebSocket(`ws://${location.host}/ws/${currentSessionId}`);
    let contentEl = null;
    let textBuffer = '';

    ws.onmessage = (event) => {
        const data = JSON.parse(event.data);

        if (data.type === 'text_delta') {
            if (!contentEl) contentEl = startAssistantMessage();
            textBuffer += data.content;
            contentEl.innerHTML = marked.parse(textBuffer);
            scrollToBottom();
        } else if (data.type === 'tool_call') {
            appendToolCall(data.name, data.arguments, '');
        } else if (data.type === 'tool_result') {
            updateLastToolResult(data.output);
            scrollToBottom();
        } else if (data.type === 'error') {
            if (!contentEl) contentEl = startAssistantMessage();
            contentEl.innerHTML += `<p style="color:var(--red)">Error: ${escapeHtml(data.message)}</p>`;
        } else if (data.type === 'done') {
            contentEl = null;
            textBuffer = '';
            isStreaming = false;
            sendBtn.disabled = false;
            inputEl.disabled = false;
            inputEl.focus();
        } else if (data.type === 'finish') {
            // usage info available here if needed
        }
    };

    ws.onclose = () => {
        isStreaming = false;
        sendBtn.disabled = false;
    };
}

function sendMessage() {
    const text = inputEl.value.trim();
    if (!text || isStreaming || !ws || ws.readyState !== WebSocket.OPEN) return;

    isStreaming = true;
    sendBtn.disabled = true;
    inputEl.disabled = true;
    inputEl.value = '';
    inputEl.style.height = 'auto';

    appendUserMessage(text);

    ws.send(JSON.stringify({ type: 'user_message', content: text }));
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
