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
        updateCounts();
        setStatus('Updated');
    } catch (e) {
        setStatus(e.message, true);
    }
}

// --- Collapsible sections (admin page UX) ---------------------------------

const COLLAPSE_KEY = 'admin:collapsedSections';

function loadCollapsed() {
    try { return JSON.parse(localStorage.getItem(COLLAPSE_KEY) || '{}'); }
    catch { return {}; }
}

function saveCollapsed(state) {
    try { localStorage.setItem(COLLAPSE_KEY, JSON.stringify(state)); } catch {}
}

function initAdminSections() {
    const main = document.getElementById('chat-area');
    const headings = Array.from(main.querySelectorAll(':scope > h2[id]'));
    const collapsed = loadCollapsed();

    headings.forEach((h2) => {
        const id = h2.id;
        if (!id) return;

        // Collect body elements that belong to this section (until next h2).
        const bodyEls = [];
        let el = h2.nextElementSibling;
        while (el && !el.matches('h2')) {
            bodyEls.push(el);
            el = el.nextElementSibling;
        }

        // Wrap them in a section so we can collapse the body independently.
        const section = document.createElement('section');
        section.className = 'admin-section';
        section.dataset.section = id;
        main.insertBefore(section, h2);
        section.appendChild(h2);
        const content = document.createElement('div');
        content.className = 'section-content';
        bodyEls.forEach((e) => content.appendChild(e));
        section.appendChild(content);

        // Turn the plain <h2> into a collapsible header.
        const toggle = document.createElement('button');
        toggle.type = 'button';
        toggle.className = 'section-toggle';
        toggle.setAttribute('aria-expanded', 'true');
        toggle.title = 'Collapse section';
        toggle.innerHTML = '<svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9l6 6 6-6"/></svg>';
        toggle.addEventListener('click', () => toggleSection(id));

        const title = document.createElement('span');
        title.className = 'section-title';
        title.textContent = h2.textContent.trim();

        const count = document.createElement('span');
        count.className = 'section-count';
        count.dataset.section = id;

        h2.className = 'section-heading';
        h2.innerHTML = '';
        h2.appendChild(toggle);
        h2.appendChild(title);
        h2.appendChild(count);

        if (collapsed[id]) {
            section.classList.add('collapsed');
            toggle.setAttribute('aria-expanded', 'false');
        }
    });

    // Sidebar nav: expand + smooth-scroll + flash on click.
    document.querySelectorAll('.admin-nav a').forEach((link) => {
        link.addEventListener('click', (e) => navigateSection(e, link.getAttribute('href')));
    });

    updateCounts();
}

function toggleSection(id) {
    const section = document.querySelector(`.admin-section[data-section="${CSS.escape(id)}"]`);
    if (!section) return;
    const collapsed = section.classList.toggle('collapsed');
    const toggle = section.querySelector('.section-toggle');
    if (toggle) toggle.setAttribute('aria-expanded', String(!collapsed));
    const state = loadCollapsed();
    if (collapsed) state[id] = true; else delete state[id];
    saveCollapsed(state);
}

function navigateSection(e, href) {
    const id = (href || '').replace('#', '');
    const section = document.querySelector(`.admin-section[data-section="${CSS.escape(id)}"]`);
    if (!section) return;
    if (section.classList.contains('collapsed')) toggleSection(id);
    const heading = section.querySelector('.section-heading');
    if (heading) {
        e.preventDefault();
        heading.scrollIntoView({ behavior: 'smooth', block: 'start' });
        heading.classList.remove('section-flash');
        // force reflow so the animation restarts
        void heading.offsetWidth;
        heading.classList.add('section-flash');
    }
}

function updateCounts() {
    document.querySelectorAll('.section-count').forEach((el) => {
        const id = el.dataset.section;
        const section = document.querySelector(`.admin-section[data-section="${CSS.escape(id)}"]`);
        if (!section) return;
        const tbody = section.querySelector('tbody');
        const n = tbody ? tbody.rows.length : section.querySelectorAll('.settings-group').length;
        el.textContent = String(n);
    });
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

initAdminSections();
loadAll();