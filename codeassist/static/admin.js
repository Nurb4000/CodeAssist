/* Registry admin page (review item I / FUTURE_ENHANCEMENTS "settings UI").
 * Backed entirely by the existing /api/* registry endpoints. */
const statusEl = document.getElementById('admin-status');

function escapeHtml(s) {
    return String(s ?? '').replace(/[&<>"']/g, (c) => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
}

function setStatus(msg, isError) {
    statusEl.textContent = msg || '';
    statusEl.style.color = isError ? 'var(--red, #e05252)' : 'var(--green, #4caf50)';
}

async function api(method, path, body) {
    const opts = { method, headers: { 'Content-Type': 'application/json' } };
    if (body) opts.body = JSON.stringify(body);
    const res = await fetch(path, opts);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
        throw new Error(data.detail || data.message || `HTTP ${res.status}`);
    }
    return data;
}

function cell(text) {
    const td = document.createElement('td');
    td.textContent = text === null || text === undefined ? '' : String(text);
    return td;
}

function fmtField(field) {
    if (field === null || field === undefined) return '';
    if (typeof field === 'string') {
        if (/^[{[]/.test(field)) {
            try { return JSON.stringify(JSON.parse(field)); } catch (e) { return field; }
        }
        return field;
    }
    try { return JSON.stringify(field); } catch (e) { return String(field); }
}

function row(cellsHtml) {
    const tr = document.createElement('tr');
    tr.innerHTML = cellsHtml;
    return tr;
}

function delButton(id, label, onDelete) {
    const b = document.createElement('button');
    b.className = 'admin-btn admin-btn-danger';
    b.textContent = label;
    b.onclick = async () => {
        if (!window.confirm(`Delete ${label.toLowerCase()}?`)) return;
        try {
            await onDelete(id);
            setStatus('Deleted');
            await loadAll();
        } catch (e) {
            setStatus(e.message, true);
        }
    };
    return b;
}

async function loadSkills() {
    const data = await api('GET', '/api/skills');
    const tbody = document.getElementById('skills-body');
    tbody.innerHTML = '';
    for (const s of data.skills || []) {
        tbody.appendChild(row(
            `<td class="cell-em">${escapeHtml(s.name || '')}</td>` +
            `<td>${escapeHtml(s.description || '')}</td>` +
            `<td>${escapeHtml(s.slash_command || '')}</td>` +
            `<td>${escapeHtml(s.source || '')}</td>`));
    }
}

async function loadMcp() {
    const data = await api('GET', '/api/mcp/servers');
    const tbody = document.getElementById('mcp-body');
    tbody.innerHTML = '';
    for (const s of data.servers || []) {
        const tr = row(
            `<td class="cell-em">${escapeHtml(s.name || '')}</td>` +
            `<td><code>${escapeHtml(fmtField(s.config))}</code></td>` +
            `<td>${s.enabled ? 'yes' : 'no'}</td>` +
            `<td></td>`);
        tr.lastElementChild.appendChild(delButton(s.id, 'Delete', (id) => api('DELETE', `/api/mcp/servers/${id}`)));
        tbody.appendChild(tr);
    }
}

async function loadLsp() {
    const data = await api('GET', '/api/lsp/servers');
    const tbody = document.getElementById('lsp-body');
    tbody.innerHTML = '';
    for (const s of data || []) {
        const tr = row(
            `<td class="cell-em">${escapeHtml(s.name || '')}</td>` +
            `<td><code>${escapeHtml(s.command || '')}</code></td>` +
            `<td>${escapeHtml(fmtField(s.args))}</td>` +
            `<td>${escapeHtml(fmtField(s.languages))}</td>` +
            `<td></td>`);
        tr.lastElementChild.appendChild(delButton(s.id, 'Delete', (id) => api('DELETE', `/api/lsp/servers/${id}`)));
        tbody.appendChild(tr);
    }
}

async function loadPlugins() {
    const data = await api('GET', '/api/plugins');
    const tbody = document.getElementById('plugins-body');
    tbody.innerHTML = '';
    for (const p of data.plugins || []) {
        tbody.appendChild(row(
            `<td class="cell-em">${escapeHtml(p.name || '')}</td>` +
            `<td>${escapeHtml(p.version || '')}</td>` +
            `<td>${p.enabled !== false ? 'yes' : 'no'}</td>`));
    }
}

async function loadCustomTools() {
    const data = await api('GET', '/api/custom-tools');
    const tbody = document.getElementById('custom-body');
    tbody.innerHTML = '';
    for (const t of data.tools || []) {
        tbody.appendChild(row(
            `<td class="cell-em">${escapeHtml(t.name || '')}</td>` +
            `<td>${escapeHtml(t.description || '')}</td>`));
    }
}

async function loadAgents() {
    const agents = await api('GET', '/api/agents');
    const tbody = document.getElementById('agents-body');
    tbody.innerHTML = '';
    for (const a of agents) {
        if (a.id === 'compaction' || a.name === 'compaction') continue;
        tbody.appendChild(row(
            `<td>${escapeHtml(a.id || '')}</td>` +
            `<td class="cell-em">${escapeHtml(a.name || '')}</td>` +
            `<td>${escapeHtml(a.description || '')}</td>` +
            `<td>${escapeHtml(a.model || '')}</td>`));
    }
}

async function loadAll() {
    try {
        await Promise.all([loadSkills(), loadMcp(), loadLsp(), loadPlugins(), loadCustomTools(), loadAgents()]);
        setStatus('Updated');
    } catch (e) {
        setStatus(e.message, true);
    }
}

async function bindCreate(idPrefix, buildBody) {
    const btn = document.getElementById(`${idPrefix}-create`);
    btn.onclick = async () => {
        try {
            const body = buildBody();
            await api('POST', `/api/${idPrefix === 'skill' ? 'skills' : idPrefix + '/servers'}`, body);
            setStatus('Created');
            await loadAll();
        } catch (e) {
            setStatus(e.message, true);
        }
    };
}

function parseJsonField(input, label) {
    const value = input.value.trim();
    if (!value) return [];
    try {
        const parsed = JSON.parse(value);
        if (!Array.isArray(parsed)) throw new Error('must be a JSON array');
        return parsed;
    } catch (e) {
        throw new Error(`${label} ${e.message}`);
    }
}

document.getElementById('refresh-btn').onclick = loadAll;
document.getElementById('skills-reload').onclick = async () => {
    try {
        await api('POST', '/api/skills/reload');
        setStatus('Skills reloaded');
        await loadSkills();
    } catch (e) { setStatus(e.message, true); }
};
document.getElementById('custom-reload').onclick = async () => {
    try {
        await api('POST', '/api/custom-tools/reload');
        setStatus('Custom tools reloaded');
        await loadCustomTools();
    } catch (e) { setStatus(e.message, true); }
};

bindCreate('skill', () => ({
    name: document.getElementById('skill-name').value.trim(),
    description: document.getElementById('skill-desc').value.trim(),
    content: document.getElementById('skill-content').value,
    slash_command: document.getElementById('skill-slash').value.trim() || null,
}));

bindCreate('mcp', () => {
    const configText = document.getElementById('mcp-config').value.trim();
    let config;
    try { config = configText ? JSON.parse(configText) : {}; } catch (e) { throw new Error(`Config must be valid JSON`); }
    return { name: document.getElementById('mcp-name').value.trim(), config };
});

bindCreate('lsp', () => ({
    name: document.getElementById('lsp-name').value.trim(),
    command: document.getElementById('lsp-command').value.trim(),
    args: parseJsonField(document.getElementById('lsp-args'), 'args'),
    languages: parseJsonField(document.getElementById('lsp-langs'), 'languages'),
}));

loadAll();