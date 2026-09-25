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

function editButton(label, onEdit) {
    const b = document.createElement('button');
    b.className = 'admin-btn';
    b.textContent = label;
    b.onclick = (e) => {
        e.preventDefault();
        onEdit();
    };
    return b;
}

let editModalEl = null;

function closeEditModal() {
    if (editModalEl && editModalEl.parentNode) editModalEl.remove();
    editModalEl = null;
}

// Reusable edit modal. `fields` = [{key,label,type,value,required}], type in
// {text,textarea,json,number,bool}. `onSubmit(payload)` returns a promise; on
// success the modal closes and the registry reloads.
function openEditModal(title, fields, onSubmit) {
    closeEditModal();
    const card = document.createElement('div');
    card.className = 'modal-card';
    card.innerHTML =
        `<div class="modal-label" style="margin-top:0">${escapeHtml(title)}</div>` +
        `<div class="modal-body"></div>` +
        `<div class="modal-actions"></div>`;
    const body = card.querySelector('.modal-body');
    const actions = card.querySelector('.modal-actions');
    const inputs = {};

    for (const f of fields) {
        const label = document.createElement('div');
        label.className = 'modal-label';
        label.textContent = f.label;
        body.appendChild(label);
        let inp;
        if (f.type === 'textarea' || f.type === 'json') {
            inp = document.createElement('textarea');
            inp.className = 'modal-textarea';
        } else {
            inp = document.createElement('input');
            inp.className = 'modal-input';
            if (f.type === 'number') inp.type = 'number';
        }
        inp.dataset.key = f.key;
        inp.value = f.value ?? '';
        if (f.required) inp.required = true;
        body.appendChild(inp);
        inputs[f.key] = inp;
    }

    const cancel = document.createElement('button');
    cancel.className = 'modal-btn';
    cancel.textContent = 'Cancel';
    cancel.onclick = closeEditModal;

    const save = document.createElement('button');
    save.className = 'modal-btn primary';
    save.textContent = 'Save';
    actions.appendChild(cancel);
    actions.appendChild(save);

    const collect = () => {
        const payload = {};
        for (const f of fields) {
            const inp = inputs[f.key];
            const raw = inp.value;
            if (f.type === 'bool') { payload[f.key] = inp.checked; continue; }
            if (f.type === 'number') {
                payload[f.key] = raw.trim() === '' ? null : Number(raw);
                continue;
            }
            if (f.type === 'json') {
                const t = raw.trim();
                payload[f.key] = t === '' ? [] : JSON.parse(t);
                continue;
            }
            payload[f.key] = raw;
        }
        return payload;
    };

    save.onclick = async () => {
        let payload;
        try {
            payload = collect();
        } catch (e) {
            setStatus('Invalid JSON: ' + e.message, true);
            return;
        }
        try {
            await onSubmit(payload);
            closeEditModal();
            setStatus('Saved');
            await loadAll();
        } catch (e) {
            setStatus(e.message, true);
        }
    };

    editModalEl = card;
    const backdrop = document.createElement('div');
    backdrop.className = 'modal-backdrop';
    backdrop.appendChild(card);
    backdrop.addEventListener('click', (e) => {
        if (e.target === backdrop) closeEditModal();
    });
    document.body.appendChild(backdrop);
    const first = card.querySelector('.modal-input, .modal-textarea');
    if (first) first.focus();
}

async function loadSkills() {
    const data = await api('GET', '/api/skills');
    const tbody = document.getElementById('skills-body');
    tbody.innerHTML = '';
    for (const s of data.skills || []) {
        const tr = row(
            `<td class="cell-em">${escapeHtml(s.name || '')}</td>` +
            `<td>${escapeHtml(s.description || '')}</td>` +
            `<td>${escapeHtml(s.slash_command || '')}</td>` +
            `<td>${escapeHtml(s.source || '')}</td>`);
        const actions = tr.lastElementChild;
        actions.appendChild(editButton('Edit', () =>
            openEditModal(`Skill: ${s.name}`, [
                { key: 'description', label: 'Description', type: 'textarea', value: s.description || '' },
                { key: 'slash_command', label: 'Slash command', type: 'text', value: s.slash_command || '' },
            ], async (p) => {
                await api('PUT', `/api/skills/${encodeURIComponent(s.name)}`, {
                    description: p.description,
                    slash_command: p.slash_command || null,
                });
            })));
        actions.appendChild(delButton(s.name, 'skill', (name) =>
            api('DELETE', `/api/skills/${encodeURIComponent(name)}`)));
        tbody.appendChild(tr);
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
        const actions = tr.lastElementChild;
        actions.appendChild(editButton('Edit', () =>
            openEditModal(`MCP server: ${s.name}`, [
                { key: 'name', label: 'Name', type: 'text', value: s.name, required: true },
                { key: 'config', label: 'Config (JSON)', type: 'json', value: fmtField(s.config) },
                { key: 'enabled', label: 'Enabled', type: 'bool', value: !!s.enabled },
            ], async (p) => {
                await api('PUT', `/api/mcp/servers/${encodeURIComponent(s.id)}`, {
                    name: p.name, config: p.config, enabled: p.enabled,
                });
            })));
        actions.appendChild(delButton(s.id, 'Delete', (id) => api('DELETE', `/api/mcp/servers/${id}`)));
        tbody.appendChild(tr);
    }
}

async function loadLsp() {
    const data = await api('GET', '/api/lsp/servers');
    const tbody = document.getElementById('lsp-body');
    tbody.innerHTML = '';
    for (const s of data.servers || []) {
        const tr = row(
            `<td class="cell-em">${escapeHtml(s.name || '')}</td>` +
            `<td><code>${escapeHtml(s.command || '')}</code></td>` +
            `<td>${escapeHtml(fmtField(s.args))}</td>` +
            `<td>${escapeHtml(fmtField(s.languages))}</td>` +
            `<td></td>`);
        const actions = tr.lastElementChild;
        actions.appendChild(editButton('Edit', () =>
            openEditModal(`LSP server: ${s.name}`, [
                { key: 'name', label: 'Name', type: 'text', value: s.name, required: true },
                { key: 'command', label: 'Command', type: 'text', value: s.command || '', required: true },
                { key: 'args', label: 'Args (JSON)', type: 'json', value: fmtField(s.args) },
                { key: 'languages', label: 'Languages (JSON)', type: 'json', value: fmtField(s.languages) },
                { key: 'enabled', label: 'Enabled', type: 'bool', value: !!s.enabled },
            ], async (p) => {
                await api('PUT', `/api/lsp/servers/${encodeURIComponent(s.id)}`, {
                    name: p.name, command: p.command, args: p.args, languages: p.languages, enabled: p.enabled,
                });
            })));
        actions.appendChild(delButton(s.id, 'Delete', (id) => api('DELETE', `/api/lsp/servers/${id}`)));
        tbody.appendChild(tr);
    }
}

async function loadPlugins() {
    const data = await api('GET', '/api/plugins');
    const tbody = document.getElementById('plugins-body');
    tbody.innerHTML = '';
    for (const p of data.plugins || []) {
        const tr = row(
            `<td class="cell-em">${escapeHtml(p.name || '')}</td>` +
            `<td>${escapeHtml(p.version || '')}</td>` +
            `<td>${p.enabled !== false ? 'yes' : 'no'}</td>`);
        const actions = tr.lastElementChild;
        actions.appendChild(delButton(p.name, 'plugin', (name) =>
            api('DELETE', `/api/plugins/${encodeURIComponent(name)}`)));
        tbody.appendChild(tr);
    }
}

async function loadCustomTools() {
    const data = await api('GET', '/api/custom-tools');
    const tbody = document.getElementById('custom-body');
    tbody.innerHTML = '';
    for (const t of data.tools || []) {
        const tr = row(
            `<td class="cell-em">${escapeHtml(t.name || '')}</td>` +
            `<td>${escapeHtml(t.description || '')}</td>`);
        const actions = tr.lastElementChild;
        actions.appendChild(delButton(t.name, 'custom tool', (name) =>
            api('DELETE', `/api/custom-tools/${encodeURIComponent(name)}`)));
        tbody.appendChild(tr);
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
            const actions = tr.lastElementChild;
            actions.appendChild(editButton('Edit', () =>
                openEditModal(`Agent: ${a.name}`, [
                    { key: 'description', label: 'Description', type: 'textarea', value: a.description || '' },
                    { key: 'instructions', label: 'Instructions', type: 'textarea', value: a.instructions || '' },
                    { key: 'model', label: 'Model', type: 'text', value: a.model || '' },
                    { key: 'max_iterations', label: 'Max iterations', type: 'number', value: a.max_iterations ?? '' },
                    { key: 'steps', label: 'Step budget', type: 'number', value: a.steps ?? '' },
                ], async (p) => {
                    await api('PATCH', `/api/agents/${encodeURIComponent(a.id)}`, {
                        description: p.description || null,
                        instructions: p.instructions || null,
                        model: p.model || null,
                        max_iterations: p.max_iterations,
                        steps: p.steps,
                    });
                })));
            actions.appendChild(delButton(a.id, 'Delete', (id) => api('DELETE', `/api/agents/${encodeURIComponent(id)}`)));
        }
        tbody.appendChild(tr);
    }
}

async function loadAll() {
    try {
        await Promise.all([loadHealth(), loadSkills(), loadMcp(), loadLsp(), loadPlugins(), loadCustomTools(), loadAgents(), loadSettings()]);
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
    const states = loadCollapsed();

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

        // Default to collapsed for a cleaner view; only force-open when the user
        // has explicitly expanded this section before (persisted as `false`).
        const isCollapsed = states[id] !== false;
        if (isCollapsed) {
            section.classList.add('collapsed');
            toggle.setAttribute('aria-expanded', 'false');
        } else {
            section.classList.remove('collapsed');
            toggle.setAttribute('aria-expanded', 'true');
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
    state[id] = collapsed; // true = collapsed, false = expanded (explicit)
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
        const groups = section.querySelectorAll('.settings-group').length;
        // Sections without a table or settings groups (e.g. Health) get no count badge.
        if (!tbody && groups === 0) { el.textContent = ''; return; }
        el.textContent = String(tbody ? tbody.rows.length : groups);
    });
}

// --- Health panel ---

async function loadHealth() {
    const body = document.getElementById('health-body');
    if (!body) return;
    let cfg = {};
    let health = { status: 'unknown' };
    let st = null;
    try { cfg = await api('GET', '/api/config'); } catch (e) { /* config optional */ }
    try { health = await api('GET', '/health'); } catch (e) { /* health optional */ }
    try { st = await api('GET', '/api/status'); } catch (e) { /* status optional */ }

    const effModel = cfg.effective_model || cfg.model || '—';
    const provider = cfg.provider || '—';
    let conn;
    if (cfg.backend_source === 'backend') {
        conn = 'Connected (auto-detected from backend)';
    } else if (cfg.backend_external) {
        conn = 'External provider (not auto-probed)';
    } else {
        conn = health.status === 'ok' ? 'Configured' : String(health.status || 'unknown');
    }

    const rows = [
        ['Server', health.status || 'unknown'],
        ['Model', `${effModel} (${provider})`],
        ['LLM connectivity', conn],
        ['Workspace', cfg.workspace || '—'],
    ];
    if (st) {
        rows.push(['Database', st.db_path || '—']);
        rows.push(['DB size', st.db_size_human || '—']);
        rows.push(['Restart needed', st.restart_needed
            ? `Yes (${st.restart_count} setting${st.restart_count === 1 ? '' : 's'})`
            : 'No']);
    } else {
        rows.push(['Database', 'unavailable']);
    }
    body.innerHTML = rows
        .map(([k, v]) => `<tr><td>${escapeHtml(k)}</td><td class="cell-em">${escapeHtml(v)}</td></tr>`)
        .join('');
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
            const sourceClass = spec.source === 'file' ? 'src-file' : 'src-ui';
            // runtime = ephemeral session-scoped value set from this page.
            const sourceLabels = { ui: 'overridden', runtime: 'this session', file: 'default' };
            const sourceLabel = sourceLabels[spec.source] || 'default';
            const restartNote = spec.restart_required ? ' · restart required' : '';
            const resetBtn = spec.source !== 'file'
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
        // Only real form controls carry editable values; the per-row Reset
        // buttons also have data-key and would overwrite the field with ''.
        document.querySelectorAll('input[data-key], select[data-key]').forEach((inp) => {
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
                const models = res.models || [];
                const modelInput = document.getElementById('set-llm.model');
                let msg = `Connected: ${models.length} model(s) — ${res.endpoint}`;
                // Reflect what the backend actually serves in the Model field
                // instead of leaving a stale default (e.g. "gpt-4o"). The user
                // explicitly probed, so adopting the detected model is expected;
                // they can retype to override. This also prevents the
                // "Model cannot be empty" save error when the field was cleared.
                if (models.length && modelInput) {
                    modelInput.value = models[0];
                    msg += ` · model set to "${models[0]}"`;
                } else if (!models.length) {
                    msg += ' · backend listed no models';
                }
                // Adopt the detected context window for local backends (e.g.
                // llama.cpp's real n_ctx) instead of leaving the 128k default.
                // Retype to override; only populated when the backend advertises one.
                if (res.context_window) {
                    const ctxInput = document.getElementById('set-llm.context_window');
                    if (ctxInput) {
                        ctxInput.value = res.context_window;
                        msg += ` · context window set to ${res.context_window}`;
                    }
                }
                setStatus(msg);
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
document.getElementById('health-refresh').onclick = async () => {
    try {
        await loadHealth();
        setStatus('Health refreshed');
    } catch (e) {
        setStatus(e.message, true);
    }
};
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
document.getElementById('mcp-reload').onclick = async () => {
    try {
        const res = await api('POST', '/api/mcp/reload');
        const n = (res.reconnected || []).length;
        setStatus(n ? `Reconnected ${n} MCP server(s)` : 'MCP connections up to date');
        await loadMcp();
    } catch (e) { setStatus(e.message, true); }
};
document.getElementById('lsp-reload').onclick = async () => {
    try {
        await api('POST', '/api/lsp/reload');
        setStatus('LSP connections re-synced');
        await loadLsp();
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