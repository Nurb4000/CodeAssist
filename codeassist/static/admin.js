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
        const tr = row(
            `<td>${escapeHtml(a.id || '')}</td>` +
            `<td class="cell-em">${escapeHtml(a.name || '')}</td>` +
            `<td>${escapeHtml(a.description || '')}</td>` +
            `<td>${escapeHtml(a.model || '')}</td>` +
            `<td></td>`);
        if (a.builtin) {
            const label = document.createElement('span');
            label.className = 'admin-muted';
            label.textContent = 'built-in';
            tr.lastElementChild.appendChild(label);
        } else {
            tr.lastElementChild.appendChild(
                delButton(a.id, 'Delete', (id) => api('DELETE', `/api/agents/${encodeURIComponent(id)}`))
            );
        }
        tbody.appendChild(tr);
    }
}

async function loadAll() {
    try {
        await Promise.all([loadSkills(), loadMcp(), loadLsp(), loadPlugins(), loadCustomTools(), loadAgents(), loadSettings()]);
        setStatus('Updated');
    } catch (e) {
        setStatus(e.message, true);
    }
}

// --- Settings tab ---

let settingsList = [];

function settingsInput(spec) {
    const id = `set-${spec.key}`;
    if (spec.type === 'bool') {
        return `<input type="checkbox" id="${id}" data-key="${spec.key}" ${spec.value ? 'checked' : ''}>`;
    }
    if (spec.options) {
        const opts = (spec.options || [])
            .map((o) => `<option value="${escapeHtml(o)}"${o === spec.value ? ' selected' : ''}>${escapeHtml(o)}</option>`)
            .join('');
        return `<select id="${id}" data-key="${spec.key}">${opts}</select>`;
    }
    const isNumber = spec.type === 'int' || spec.type === 'float';
    const placeholder = spec.has_value ? '•••••• (saved override)' : '';
    const value = spec.has_value ? '' : String(spec.value ?? '');
    return `<input type="${isNumber ? 'number' : 'text'}" id="${id}" data-key="${spec.key}" value="${escapeHtml(value)}" placeholder="${escapeHtml(placeholder)}">`;
}

async function loadSettings() {
    const data = await api('GET', '/api/settings');
    settingsList = data.settings;
    const groups = {};
    for (const s of settingsList) {
        (groups[s.group] = groups[s.group] || []).push(s);
    }
    const container = document.getElementById('settings-body');
    container.innerHTML = '';
    for (const [group, items] of Object.entries(groups)) {
        const wrapper = document.createElement('div');
        wrapper.className = 'settings-group';
        wrapper.innerHTML = `<h3>${escapeHtml(group)}</h3>`;
        const table = document.createElement('table');
        table.className = 'admin-table';
        table.innerHTML = '<thead><tr><th>Setting</th><th>Value</th></tr></thead>';
        const tbody = document.createElement('tbody');
        for (const spec of items) {
            const sourceClass = spec.source === 'ui' ? 'src-ui' : 'src-file';
            const sourceLabel = spec.source === 'ui' ? 'overridden' : 'from config.toml';
            const restartNote = spec.restart_required ? ' · restart required' : '';
            const resetBtn = spec.source === 'ui'
                ? `<button class="admin-btn admin-btn-danger settings-reset" data-key="${spec.key}">Reset</button>`
                : '';
            tbody.appendChild(row(
                `<td>
                    <span class="cell-em">${escapeHtml(spec.label)}</span>
                    <div class="admin-muted small">${escapeHtml(spec.description || '')}</div>
                    <span class="settings-source ${sourceClass}">${sourceLabel}${restartNote}</span>
                </td>
                <td>${settingsInput(spec)} ${resetBtn}</td>`));
        }
        table.appendChild(tbody);
        wrapper.appendChild(table);
        container.appendChild(wrapper);
    }
    container.querySelectorAll('.settings-reset').forEach((b) => {
        b.onclick = async () => {
            try {
                await api('DELETE', `/api/settings/${encodeURIComponent(b.dataset.key)}`);
                setStatus('Reset to file/default');
                await loadSettings();
            } catch (e) {
                setStatus(e.message, true);
            }
        };
    });
}

function bindSettingsActions() {
    document.getElementById('settings-save').onclick = async () => {
        const payload = {};
        document.querySelectorAll('[data-key]').forEach((inp) => {
            const spec = settingsList.find((s) => s.key === inp.dataset.key);
            if (!spec) return;
            if (spec.type === 'bool') {
                payload[spec.key] = inp.checked;
                return;
            }
            const val = inp.value;
            if (spec.has_value && !val.trim()) return; // secret placeholder = keep unchanged
            payload[spec.key] = val;
        });
        try {
            const res = await api('PUT', '/api/settings', payload);
            const note = res.restart_required && res.restart_required.length
                ? ` Restart required: ${res.restart_required.join(', ')}`
                : '';
            setStatus(`Saved ${res.applied.length} setting(s).${note}`);
            await loadSettings();
        } catch (e) {
            setStatus(e.message, true);
        }
    };

    document.getElementById('settings-test').onclick = async () => {
        const baseInput = document.getElementById('set-llm.base_url');
        const keyInput = document.getElementById('set-llm.api_key');
        setStatus('Testing LLM connection…');
        try {
            const res = await api('POST', '/api/config/test-connection', {
                base_url: (baseInput && baseInput.value.trim()) || undefined,
                api_key: (keyInput && keyInput.value.trim()) || undefined,
            });
            if (res.ok) {
                setStatus(`Connected: ${(res.models || []).length} model(s) — ${res.endpoint}`);
            } else {
                setStatus(`Connection failed: ${res.error || 'unknown error'}`, true);
            }
        } catch (e) {
            setStatus(e.message, true);
        }
    };
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

bindSettingsActions();

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

document.getElementById('agent-create').onclick = async () => {
    const name = document.getElementById('agent-name').value.trim();
    if (!name) { setStatus('Agent key is required', true); return; }
    try {
        await api('POST', '/api/agents', {
            name,
            description: document.getElementById('agent-desc').value.trim() || null,
            model: document.getElementById('agent-model').value.trim() || null,
            instructions: document.getElementById('agent-instr').value.trim() || null,
        });
        setStatus('Agent created');
        document.getElementById('agent-name').value = '';
        document.getElementById('agent-desc').value = '';
        document.getElementById('agent-model').value = '';
        document.getElementById('agent-instr').value = '';
        await loadAgents();
    } catch (e) { setStatus(e.message, true); }
};

loadAll();