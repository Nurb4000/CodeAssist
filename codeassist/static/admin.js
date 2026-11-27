/* Registry admin page (review item I / FUTURE_ENHANCEMENTS "settings UI").
 * Backed entirely by the existing /api/* registry endpoints. */
const statusEl = document.getElementById('admin-status');

// escapeHtml is the shared helper loaded from /static/shared/escapeHtml.js
// before this script; kept here as a one-line pointer so a re-add is obvious.

/* Alphabetical by name, case-insensitively. Declared as a named function and
 * called from the loop head rather than written as a multi-line arrow inside
 * `for (... of ...)`: the inline form is a SyntaxError, which kills this whole
 * script and leaves the page with its static HTML and nothing else. */
function byName(a, b) {
    return String(a && a.name || '').localeCompare(String(b && b.name || ''));
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

function delButton(id, label, onDelete, confirmText) {
    const b = document.createElement('button');
    b.className = 'admin-btn admin-btn-danger';
    b.textContent = label;
    b.onclick = async () => {
        if (!window.confirm(confirmText || `Delete ${label.toLowerCase()}?`)) return;
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
// The keydown handler that closes the edit modal on Escape. Held at module
// scope so closeEditModal() can remove exactly the listener openEditModal()
// added -- otherwise each open would stack another listener and none would ever
// be cleaned up.
let editModalEscapeHandler = null;
// The keydown handler that traps Tab focus inside the edit modal. Held at module
// scope so closeEditModal() can remove exactly the listener openEditModal()
// added -- otherwise each open would stack another listener and none would ever
// be cleaned up.
let editModalFocusHandler = null;

function closeEditModal() {
    if (editModalEl && editModalEl.parentNode) editModalEl.remove();
    if (editModalEscapeHandler) {
        window.removeEventListener('keydown', editModalEscapeHandler);
        editModalEscapeHandler = null;
    }
    if (editModalFocusHandler) {
        window.removeEventListener('keydown', editModalFocusHandler);
        editModalFocusHandler = null;
    }
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
        if (f.type === 'bool') {
            // A checkbox, not a text input: collect() reads `inp.checked`, which
            // is always undefined (→ false) on a text input, so editing an
            // enabled MCP/LSP server silently disabled it on every save.
            inp = document.createElement('input');
            inp.className = 'modal-input';
            inp.type = 'checkbox';
            inp.checked = !!f.value;
        } else if (f.type === 'textarea' || f.type === 'json') {
            inp = document.createElement('textarea');
            inp.className = 'modal-textarea';
        } else {
            inp = document.createElement('input');
            inp.className = 'modal-input';
            if (f.type === 'number') inp.type = 'number';
        }
        inp.dataset.key = f.key;
        if (f.type !== 'bool') inp.value = f.value ?? '';
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

    const backdrop = document.createElement('div');
    backdrop.className = 'modal-backdrop';
    backdrop.appendChild(card);
    // Track the backdrop (not the nested card) so closeEditModal() removes the
    // whole overlay — removing just the card would leave the grey backdrop stuck
    // on body and freeze the screen until a refresh.
    editModalEl = backdrop;
    backdrop.addEventListener('click', (e) => {
        if (e.target === backdrop) closeEditModal();
    });
    document.body.appendChild(backdrop);
    const first = card.querySelector('.modal-input, .modal-textarea');
    if (first) first.focus();

    // Escape closes the modal. Without this the only ways out were Cancel or
    // clicking the backdrop -- keyboard users (and habit) expect Esc to dismiss
    // an overlay. Listened on window so it fires regardless of where focus is,
    // and removed in closeEditModal() so handlers do not stack across opens.
    editModalEscapeHandler = (e) => {
        if (e.key === 'Escape' && editModalEl === backdrop) {
            e.preventDefault();
            closeEditModal();
        }
    };
    window.addEventListener('keydown', editModalEscapeHandler);

    // Focus trap: keep Tab / Shift+Tab focus inside the modal so keyboard users
    // cannot tab behind the backdrop to the page controls (nav, section toggles)
    // it covers. On Tab at the last focusable element, wrap to the first; on
    // Shift+Tab at the first, wrap to the last. Listened on window (like Escape)
    // and guarded by editModalEl === backdrop so a stale handler cannot move
    // focus for a newer modal. Focusables are collected live each Tab so a field
    // added/removed between opens is still trapped.
    const focusables = () => Array.from(
        backdrop.querySelectorAll(
            'input:not([type="hidden"]), textarea, select, button, [tabindex]:not([tabindex="-1"])',
        ),
    ).filter((el) => !el.disabled);
    editModalFocusHandler = (e) => {
        if (e.key !== 'Tab' || editModalEl !== backdrop) return;
        const list = focusables();
        if (!list.length) return;
        const idx = list.indexOf(document.activeElement);
        if (e.shiftKey && idx === 0) {
            e.preventDefault();
            list[list.length - 1].focus();
        } else if (!e.shiftKey && idx === list.length - 1) {
            e.preventDefault();
            list[0].focus();
        }
    };
    window.addEventListener('keydown', editModalFocusHandler);
}

async function loadSkills() {
    const data = await api('GET', '/api/skills');
    const tbody = document.getElementById('skills-body');
    tbody.innerHTML = '';
    for (const s of (data.skills || []).sort(byName)) {
        const tr = row(
            `<td class="cell-em">${escapeHtml(s.name || '')}</td>` +
            `<td>${escapeHtml(s.description || '')}</td>` +
            `<td>${escapeHtml(s.slash_command || '')}</td>` +
            `<td>${escapeHtml(s.source || '')}</td>` +
            '<td></td>');
        const actions = tr.lastElementChild;
        // Custom skills are the only ones the user owns, so they are the only
        // ones that get edit/delete. Base skills ship with the app and packaged
        // ones live in the installed package: neither may be touched from here.
        // A missing or unrecognised category therefore gets no actions either --
        // a missing Delete button is cosmetic, a Delete button on a base skill
        // destroys a shipped file.
        if (s.category === 'custom') {
            actions.appendChild(editButton('Edit', () =>
                openEditModal(`Custom skill: ${s.name}`, [
                    { key: 'description', label: 'Description', type: 'textarea', value: s.description || '' },
                    { key: 'slash_command', label: 'Slash command', type: 'text', value: s.slash_command || '' },
                ], async (p) => {
                    await api('PUT', `/api/skills/${encodeURIComponent(s.name)}`, {
                        description: p.description,
                        slash_command: p.slash_command || null,
                    });
                })));
            actions.appendChild(delButton(s.name, 'Delete',
                (name) => api('DELETE', `/api/skills/${encodeURIComponent(name)}`),
                `Delete custom skill "${s.name}"? Its file is removed from the workspace.`));
        } else {
            actions.className = 'admin-muted';
            actions.textContent = 'read-only';
        }
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

function mcpNames(items, key) {
    if (!Array.isArray(items) || !items.length) return '—';
    const labels = items.map((item) =>
        typeof item === 'string' ? item : (item && (item[key] || item.name || item.uri)) || ''
    ).filter(Boolean);
    return labels.length ? labels.join(', ') : '—';
}

async function loadMcpStatus() {
    const el = document.getElementById('mcp-status');
    if (!el) return;
    const data = await api('GET', '/api/mcp/status');
    el.innerHTML = '';
    if (!data.enabled) {
        el.textContent = 'MCP is disabled';
        return;
    }
    const servers = data.servers || [];
    if (!servers.length) {
        el.textContent = 'No MCP servers configured';
        return;
    }
    for (const s of servers) {
        const entry = document.createElement('div');
        const headline = document.createElement('div');
        headline.textContent = `${s.name || 'unnamed'} · ${s.transport || 'http'} · ${s.connected ? 'connected' : 'disconnected'}`;
        entry.appendChild(headline);

        const details = document.createElement('div');
        details.className = 'admin-muted';
        details.textContent = `tools: ${mcpNames(s.tools)} | prompts: ${mcpNames(s.prompts)} | resources: ${mcpNames(s.resources, 'uri')}`;
        entry.appendChild(details);

        if (s.oauth_provider) {
            const auth = document.createElement('div');
            auth.className = 'admin-muted';
            auth.textContent = `OAuth: ${s.oauth_provider}`;
            entry.appendChild(auth);
        }
        if (s.instructions) {
            const notes = document.createElement('div');
            notes.className = 'admin-muted';
            notes.textContent = `instructions: ${s.instructions}`;
            entry.appendChild(notes);
        }
        if (s.error) {
            const err = document.createElement('div');
            err.style.color = 'var(--red, #e05252)';
            err.textContent = `error: ${s.error}`;
            entry.appendChild(err);
        }
        el.appendChild(entry);
    }
}

async function loadAutomations() {
    const data = await api('GET', '/api/automations');
    const tbody = document.getElementById('automation-body');
    if (!tbody) return;
    tbody.innerHTML = '';
    for (const a of data.automations || []) {
        const next = a.next_run_at ? String(a.next_run_at).slice(0, 19).replace('T', ' ') : '—';
        const tr = row(
            `<td class="cell-em">${escapeHtml(a.name || '')}</td>` +
            `<td>${escapeHtml(a.kind || '')}</td>` +
            `<td><code>${escapeHtml(a.schedule || '')}</code></td>` +
            `<td>${escapeHtml(a.action || '')}</td>` +
            `<td>${escapeHtml(next)}</td>` +
            `<td>${escapeHtml(a.last_status || 'never')}</td>` +
            `<td></td>`);
        const actions = tr.lastElementChild;
        const run = document.createElement('button');
        run.className = 'admin-btn';
        run.textContent = 'Run now';
        run.addEventListener('click', async () => {
            try {
                await api('POST', `/api/automations/${encodeURIComponent(a.id)}/run`);
                setStatus('Automation queued');
                await loadAutomations();
            } catch (e) { setStatus(e.message, true); }
        });
        actions.appendChild(run);
        const history = document.createElement('button');
        history.className = 'admin-btn';
        history.textContent = 'Runs';
        history.addEventListener('click', async () => {
            const existing = tr.nextElementSibling;
            if (existing && existing.classList.contains('automation-runs-row')) {
                existing.remove();
                return;
            }
            const detail = document.createElement('tr');
            detail.className = 'automation-runs-row';
            detail.innerHTML = '<td colspan="7"><pre>Loading runs…</pre></td>';
            tr.after(detail);
            try {
                const data = await api('GET', `/api/automations/${encodeURIComponent(a.id)}/runs?limit=20`);
                const lines = (data.runs || []).map((r) =>
                    `${r.started_at}  ${r.status}${r.error ? '  ' + r.error : ''}` +
                    (r.output ? '\n' + r.output : '')
                );
                detail.querySelector('pre').textContent = lines.length ? lines.join('\n\n') : 'No runs yet';
            } catch (e) {
                detail.querySelector('pre').textContent = e.message;
            }
        });
        actions.appendChild(history);
        actions.appendChild(delButton(a.id, 'Delete', (id) => api('DELETE', `/api/automations/${id}`)));
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
            `<td>${p.enabled !== false ? 'yes' : 'no'}</td>` +
            '<td></td>');
        const actions = tr.lastElementChild;
        actions.appendChild(delButton(p.name, 'Delete',
            (name) => api('DELETE', `/api/plugins/${encodeURIComponent(name)}`),
            `Delete plugin "${p.name}"?`));
        tbody.appendChild(tr);
    }
}

async function loadCustomTools() {
    const data = await api('GET', '/api/custom-tools');
    const tbody = document.getElementById('custom-body');
    tbody.innerHTML = '';
    for (const t of (data.tools || []).sort(byName)) {
        const tr = row(
            `<td class="cell-em">${escapeHtml(t.name || '')}</td>` +
            `<td>${escapeHtml(t.description || '')}</td>` +
            '<td></td>');
        const actions = tr.lastElementChild;
        actions.appendChild(delButton(t.name, 'Delete',
            (name) => api('DELETE', `/api/custom-tools/${encodeURIComponent(name)}`),
            `Delete custom tool "${t.name}"?`));
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
            const actions = tr.lastElementChild;
            // Built-ins expose only the tunable per-agent step budget; their
            // permissions/instructions are code-defined and not editable.
            actions.appendChild(editButton('Edit', () =>
                openEditModal(`Agent: ${a.name}`, [
                    { key: 'steps', label: 'Step budget', type: 'number', value: a.steps ?? '' },
                ], async (p) => {
                    await api('PATCH', `/api/agents/${encodeURIComponent(a.id)}`, { steps: p.steps });
                })));
            actions.appendChild(stepChip(a));
            const label = document.createElement('span');
            label.className = 'admin-muted';
            label.textContent = 'built-in';
            actions.appendChild(label);
        } else {
            const actions = tr.lastElementChild;
            actions.appendChild(stepChip(a));
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

// Small muted chip showing an agent's current step budget so it's visible at a
// glance without opening the edit modal.
function stepChip(a) {
    const chip = document.createElement('span');
    chip.className = 'admin-muted';
    chip.textContent = 'Steps: ' + (a.steps != null ? a.steps : '—');
    return chip;
}

async function loadAll() {
    try {
        await Promise.all([loadHealth(), loadSkills(), loadMcp(), loadMcpStatus(), loadLsp(), loadPlugins(), loadCustomTools(), loadAgents(), loadProfiles(), loadSettings(), loadAutomations()]);
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
    if (spec.key === 'llm.model') {
        // Native datalist gives a searchable/filterable picker while keeping the
        // field freely editable (custom model ids, manual overrides).
        return `<input type="text" id="${id}" data-key="${spec.key}" list="llm-model-options" value="${escapeHtml(spec.value ?? '')}">`;
    }
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
        if (s.hidden) continue; // e.g. llm.active_profile has its own toolbar
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
            const sourceLabels = { ui: 'overridden', runtime: 'this session', file: 'default', profile: 'profile' };
            const sourceLabel = sourceLabels[spec.source] || 'default';
            const restartNote = spec.restart_required ? ' · restart required' : '';
            // Profile fields are edited on the profile itself; resetting a flat
            // override would be a no-op and misleading while one is active.
            const resetBtn = spec.source !== 'file' && spec.source !== 'profile'
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

// --- Provider profiles -----------------------------------------------------

let profileList = [];

function setProfileField(id, value) {
    const el = document.getElementById(id);
    if (el) el.value = value ?? '';
}

function fillProfileForm(profile) {
    if (!profile) return;
    setProfileField('profile-name', profile.name);
    setProfileField('profile-provider', profile.provider || 'openai');
    setProfileField('profile-base-url', profile.base_url || '');
    setProfileField('profile-api-key', ''); // never echo a stored secret
    setProfileField('profile-model', profile.model || '');
    setProfileField('profile-context-window', profile.context_window || 128000);
    setProfileField('profile-timeout', profile.timeout || 900);
}

async function loadProfiles() {
    const data = await api('GET', '/api/provider-profiles');
    profileList = data.profiles || [];
    const select = document.getElementById('provider-profile-select');
    select.innerHTML = '';
    const blank = document.createElement('option');
    blank.value = '';
    blank.textContent = '(file / default)';
    select.appendChild(blank);
    for (const p of profileList) {
        const opt = document.createElement('option');
        opt.value = p.name;
        opt.textContent = p.name;
        select.appendChild(opt);
    }
    select.value = data.active || '';
    if (data.active) fillProfileForm(profileList.find((p) => p.name === data.active));
}

function bindProfileActions() {
    document.getElementById('provider-profile-activate').onclick = async () => {
        const name = document.getElementById('provider-profile-select').value;
        try {
            await api('PUT', '/api/settings', { 'llm.active_profile': name });
            setStatus(name ? `Activated profile "${name}"` : 'Using file/default provider');
            await Promise.all([loadProfiles(), loadSettings()]);
        } catch (e) {
            setStatus(e.message, true);
        }
    };
    document.getElementById('provider-profile-delete').onclick = async () => {
        const name = document.getElementById('provider-profile-select').value;
        if (!name) { setStatus('Select a profile to delete', true); return; }
        if (!window.confirm(`Delete provider profile "${name}"?`)) return;
        try {
            await api('DELETE', `/api/provider-profiles/${encodeURIComponent(name)}`);
            setStatus(`Deleted profile "${name}"`);
            document.getElementById('provider-profile-select').value = '';
            await Promise.all([loadProfiles(), loadSettings()]);
        } catch (e) {
            setStatus(e.message, true);
        }
    };
    document.getElementById('profile-save').onclick = async () => {
        const body = {
            name: document.getElementById('profile-name').value.trim(),
            provider: document.getElementById('profile-provider').value,
            base_url: document.getElementById('profile-base-url').value.trim(),
            api_key: document.getElementById('profile-api-key').value.trim(),
            model: document.getElementById('profile-model').value.trim(),
            context_window: Number(document.getElementById('profile-context-window').value || 128000),
            timeout: Number(document.getElementById('profile-timeout').value || 900),
        };
        if (!body.name) { setStatus('Profile name is required', true); return; }
        try {
            const saved = await api('POST', '/api/provider-profiles', body);
            setStatus(`Saved profile "${saved.name}"`);
            document.getElementById('provider-profile-select').value = saved.name;
            await loadProfiles();
        } catch (e) {
            setStatus(e.message, true);
        }
    };
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
        const profile = document.getElementById('provider-profile-select').value;
        setStatus('Testing LLM connection…');
        try {
            const res = await api('POST', '/api/config/test-connection', {
                profile: profile || undefined,
                base_url: (baseInput && baseInput.value.trim()) || undefined,
                api_key: (keyInput && keyInput.value.trim()) || undefined,
            });
            if (res.ok) {
                const models = res.models || [];
                const modelInput = document.getElementById('set-llm.model');
                let msg = `Connected: ${models.length} model(s) — ${res.endpoint}`;

                // Fill the native datalist so the Model field becomes a
                // searchable picker. Keep any model the user already chose.
                const datalist = document.getElementById('llm-model-options');
                if (datalist) {
                    datalist.innerHTML = '';
                    for (const m of models) {
                        const opt = document.createElement('option');
                        opt.value = m;
                        datalist.appendChild(opt);
                    }
                }
                if (models.length && modelInput && !modelInput.value.trim()) {
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

    // Local/custom backends (e.g. llama.cpp) often need no auth. When the user
    // switches to 'custom', default the API key to none: clear the field and
    // drop any stale saved override so an old key isn't sent as a Bearer header
    // against a no-auth backend (which 401s and feels like the key is required).
    const providerSelect = document.getElementById('set-llm.provider');
    if (providerSelect) {
        providerSelect.addEventListener('change', async () => {
            if (providerSelect.value !== 'custom') return;
            const keyInput = document.getElementById('set-llm.api_key');
            if (keyInput) keyInput.value = '';
            try {
                await api('DELETE', '/api/settings/llm.api_key');
                await loadSettings();
                setStatus('API key cleared for custom (local) backend — connection uses no auth. Enter a key above if your backend requires one.');
            } catch (e) {
                setStatus(e.message, true);
            }
        });
    }
}

async function bindCreate(idPrefix, buildBody, requiredFields = []) {
    const btn = document.getElementById(`${idPrefix}-create`);
    btn.onclick = async () => {
        try {
            // Validate required fields client-side so an empty submit is rejected
            // instantly with a clear message instead of round-tripping to the
            // server and failing there (agent-create already did this; the generic
            // bindCreate paths did not).
            for (const { id, label } of requiredFields) {
                const el = document.getElementById(id);
                if (!el || !el.value.trim()) {
                    throw new Error(`${label} is required`);
                }
            }
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
        await Promise.all([loadMcp(), loadMcpStatus()]);
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
bindProfileActions();

bindCreate('skill', () => ({
    name: document.getElementById('skill-name').value.trim(),
    description: document.getElementById('skill-desc').value.trim(),
    content: document.getElementById('skill-content').value,
    slash_command: document.getElementById('skill-slash').value.trim() || null,
}), [{ id: 'skill-name', label: 'Name' }]);

bindCreate('mcp', () => {
    const configText = document.getElementById('mcp-config').value.trim();
    let config;
    try { config = configText ? JSON.parse(configText) : {}; } catch (e) { throw new Error(`Config must be valid JSON`); }
    return { name: document.getElementById('mcp-name').value.trim(), config };
}, [{ id: 'mcp-name', label: 'Name' }]);

bindCreate('lsp', () => ({
    name: document.getElementById('lsp-name').value.trim(),
    command: document.getElementById('lsp-command').value.trim(),
    args: parseJsonField(document.getElementById('lsp-args'), 'args'),
    languages: parseJsonField(document.getElementById('lsp-langs'), 'languages'),
}), [
    { id: 'lsp-name', label: 'Name' },
    { id: 'lsp-command', label: 'Command' },
]);

document.getElementById('automation-create').onclick = async () => {
    const name = document.getElementById('automation-name').value.trim();
    const schedule = document.getElementById('automation-schedule').value.trim();
    const payload = document.getElementById('automation-payload').value;
    if (!name || !schedule || !payload.trim()) {
        setStatus('Name, schedule and payload are required', true);
        return;
    }
    try {
        await api('POST', '/api/automations', {
            name,
            kind: document.getElementById('automation-kind').value,
            schedule,
            action: document.getElementById('automation-action').value,
            payload,
            session_id: document.getElementById('automation-session').value.trim() || null,
        });
        setStatus('Automation created');
        ['automation-name', 'automation-schedule', 'automation-payload', 'automation-session']
            .forEach((id) => { document.getElementById(id).value = ''; });
        await loadAutomations();
    } catch (e) { setStatus(e.message, true); }
};

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
            localStorage.setItem(SIDEBAR_HIDDEN_KEY, String(!hidden));
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
            localStorage.setItem(SIDEBAR_HIDDEN_KEY, String(!hidden));
        }
    });
}

initSidebarToggle();