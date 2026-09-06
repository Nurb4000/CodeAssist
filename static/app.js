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

let configData = {};
let pendingImages = [];

const MAX_IMAGES_PER_MESSAGE = 4;
const MAX_IMAGE_BYTES = 8 * 1024 * 1024;

let currentSessionId = null;
let ws = null;
let wsConnected = false;
let isStreaming = false;
let currentContentEl = null;
let textBuffer = '';
let reconnectTimer = null;
let currentToolPanel = null;
let toolCallCount = 0;

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
    modelInfoEl.textContent = `${configData.model} | ${configData.workspace}`;
    setAttachmentUiEnabled(!!configData.vision);
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

async function addPendingImage(file) {
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
            if (hasTools) {
                if (!currentToolPanel) {
                    startAssistantMessage();
                    currentToolPanel.style.display = '';
                }
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
            <p class="welcome-hint">Hover a session name to rename or delete it</p>
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
            if (dataUrl) {
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
    div.innerHTML = `<div class="message-role assistant">CodeAssist</div><div class="message-content"></div>`;
    messagesEl.appendChild(div);
    div.querySelector('.message-content').innerHTML = marked.parse(text);
}

function startAssistantMessage() {
    removeWelcome();
    const div = document.createElement('div');
    div.className = 'message';
    div.innerHTML = `<div class="message-role assistant">CodeAssist</div><div class="tool-panel"></div><div class="message-content"></div>`;
    messagesEl.appendChild(div);
    currentContentEl = div.querySelector('.message-content');
    currentToolPanel = div.querySelector('.tool-panel');
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
    let trustHtml = '';
    if (toolName === 'write' || toolName === 'edit') {
        trustHtml = `
            <label class="trust-option">
                <input type="checkbox" id="trust-workspace-${confirmId}">
                Trust all writes in workspace
            </label>
        `;
    } else if (toolName === 'shell') {
        trustHtml = `
            <label class="trust-option">
                <input type="checkbox" id="trust-shell-${confirmId}">
                Trust all shell commands for this session
            </label>
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
        div.remove();
        showProgress(`Executing ${toolName}...`);
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({
                type: 'confirm_response',
                id: confirmId,
                approved: true,
                trust_workspace: trustWorkspace,
                trust_shell: trustShell,
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
    };

    ws.onmessage = (event) => {
        const data = JSON.parse(event.data);

        if (data.type === 'text_delta') {
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
        } else if (data.type === 'finish') {
            // usage info
        }
    };

    ws.onclose = (event) => {
        wsConnected = false;
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
    if ((!text && !hasImages) || isStreaming) return;

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
    appendUserMessage(text, images);
    clearPendingImages();
    hideContinueButton();
    showProgress('Thinking...');
    ws.send(JSON.stringify({ type: 'user_message', content: text, images }));
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
        inputEl.value = 'continue';
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
        await addPendingImage(file);
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
        addPendingImage(file);
    }
});

stopBtn.addEventListener('click', () => {
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'cancel' }));
    }
});
newSessionBtn.addEventListener('click', createSession);

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
