// Knowledge Base JavaScript

const API = {
    stats: '/api/kb/stats',
    entries: '/api/kb/entries',
    entry: (id) => `/api/kb/entries/${id}`,
    search: '/api/kb/search',
    sessions: '/api/kb/sessions',
    session: (id) => `/api/kb/sessions/${id}`,
    analyticsTools: '/api/kb/analytics/tools',
    analyticsLlm: '/api/kb/analytics/llm',
    piiScan: '/api/kb/pii/scan',
    piiRedact: '/api/kb/pii/redact',
    settings: '/api/kb/settings',
    export: '/api/kb/export',
    import: '/api/kb/import',
    clear: '/api/kb/clear',
};

// State
let currentPage = 'dashboard';
let entriesOffset = 0;
const entriesLimit = 20;

// Navigation
document.querySelectorAll('.nav-item').forEach(item => {
    item.addEventListener('click', (e) => {
        e.preventDefault();
        const page = item.dataset.page;
        navigateTo(page);
    });
});

function navigateTo(page) {
    currentPage = page;
    
    // Update nav
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    document.querySelector(`[data-page="${page}"]`).classList.add('active');
    
    // Show page
    document.querySelectorAll('.kb-page').forEach(p => p.classList.remove('active'));
    document.getElementById(`page-${page}`).classList.add('active');
    
    // Load data
    loadPage(page);
}

async function loadPage(page) {
    switch (page) {
        case 'dashboard':
            await loadDashboard();
            break;
        case 'entries':
            await loadEntries();
            break;
        case 'sessions':
            await loadSessions();
            break;
        case 'analytics':
            await loadAnalytics();
            break;
        case 'settings':
            await loadSettings();
            break;
    }
}

// Dashboard
async function loadDashboard() {
    try {
        const res = await fetch(API.stats);
        const stats = await res.json();
        
        document.getElementById('stat-total').textContent = stats.total || 0;
        document.getElementById('stat-sessions').textContent = stats.total_sessions || 0;
        document.getElementById('stat-tool-calls').textContent = stats.total_tool_calls || 0;
        document.getElementById('stat-quality').textContent = (stats.avg_quality_score || 0).toFixed(2);
        
        // Entry type chart
        const chartContainer = document.getElementById('entry-type-chart');
        const types = ['pattern', 'convention', 'decision', 'bug_fix', 'optimization', 'skill_created', 'tool_created'];
        const maxCount = Math.max(...types.map(t => stats[t] || 0), 1);
        
        chartContainer.innerHTML = types.map(type => {
            const count = stats[type] || 0;
            const width = (count / maxCount) * 100;
            return `
                <div class="chart-bar">
                    <span class="chart-label">${type.replace('_', ' ')}</span>
                    <div class="chart-value" style="width: ${Math.max(width, 5)}%">${count}</div>
                </div>
            `;
        }).join('');
        
        // Recent activity
        const activityContainer = document.getElementById('recent-activity');
        activityContainer.innerHTML = `
            <p>Last 7 days: <strong>${stats.recent_entries_7d || 0}</strong> new entries</p>
        `;
        
    } catch (err) {
        console.error('Failed to load dashboard:', err);
    }
}

// Entries
async function loadEntries() {
    const type = document.getElementById('filter-type').value;
    const scope = document.getElementById('filter-scope').value;
    const search = document.getElementById('filter-search').value;
    
    try {
        const params = new URLSearchParams({
            limit: entriesLimit,
            offset: entriesOffset,
        });
        if (type) params.set('entry_type', type);
        if (scope) params.set('scope', scope);
        if (search) params.set('search', search);
        
        const res = await fetch(`${API.entries}?${params}`);
        const data = await res.json();
        
        const tbody = document.getElementById('entries-tbody');
        if (!data.entries || data.entries.length === 0) {
            tbody.innerHTML = '<tr><td colspan="7" class="empty-state">No entries found</td></tr>';
            return;
        }
        
        tbody.innerHTML = data.entries.map(entry => `
            <tr>
                <td><input type="checkbox" class="entry-checkbox" data-id="${entry.id}"></td>
                <td><span class="type-badge ${entry.entry_type}">${entry.entry_type}</span></td>
                <td class="entry-preview">${escapeHtml(entry.content || '').substring(0, 100)}</td>
                <td>${entry.scope || '-'}</td>
                <td>${(entry.confidence || 0).toFixed(2)}</td>
                <td>${formatDate(entry.created_at)}</td>
                <td>
                    <button onclick="viewEntry('${entry.id}')">View</button>
                    <button onclick="editEntry('${entry.id}')">Edit</button>
                    <button onclick="deleteEntry('${entry.id}')" class="btn-danger">Delete</button>
                </td>
            </tr>
        `).join('');
        
        // Pagination
        renderPagination(data.count);
        
        // Bulk delete visibility
        document.getElementById('btn-bulk-delete').style.display = 
            document.querySelectorAll('.entry-checkbox:checked').length > 0 ? 'inline-block' : 'none';
        
    } catch (err) {
        console.error('Failed to load entries:', err);
    }
}

function renderPagination(total) {
    const pages = Math.ceil(total / entriesLimit);
    const currentPage = Math.floor(entriesOffset / entriesLimit) + 1;
    
    const container = document.getElementById('entries-pagination');
    let html = '';
    
    for (let i = 1; i <= pages && i <= 10; i++) {
        html += `<button class="${i === currentPage ? 'active' : ''}" onclick="goToPage(${i})">${i}</button>`;
    }
    
    container.innerHTML = html;
}

function goToPage(page) {
    entriesOffset = (page - 1) * entriesLimit;
    loadEntries();
}

// Entry actions
async function viewEntry(id) {
    try {
        const res = await fetch(API.entry(id));
        const entry = await res.json();
        
        const content = document.getElementById('view-entry-content');
        content.innerHTML = `
            <p><strong>Type:</strong> <span class="type-badge ${entry.entry_type}">${entry.entry_type}</span></p>
            <p><strong>Scope:</strong> ${entry.scope}</p>
            <p><strong>Confidence:</strong> ${(entry.confidence || 0).toFixed(2)}</p>
            <p><strong>Tags:</strong> ${entry.tags || 'None'}</p>
            <p><strong>Content:</strong></p>
            <pre>${escapeHtml(entry.content || '')}</pre>
            <p><strong>Created:</strong> ${formatDate(entry.created_at)}</p>
        `;
        
        document.getElementById('view-modal').style.display = 'flex';
    } catch (err) {
        alert('Failed to load entry');
    }
}

async function editEntry(id) {
    try {
        const res = await fetch(API.entry(id));
        const entry = await res.json();
        
        document.getElementById('edit-entry-id').value = id;
        document.getElementById('edit-content').value = entry.content || '';
        document.getElementById('edit-confidence').value = entry.confidence || 1.0;
        document.getElementById('edit-tags').value = entry.tags || '';
        
        document.getElementById('edit-modal').style.display = 'flex';
    } catch (err) {
        alert('Failed to load entry');
    }
}

async function deleteEntry(id) {
    if (!confirm('Are you sure you want to delete this entry?')) return;
    
    try {
        await fetch(API.entry(id), { method: 'DELETE' });
        loadEntries();
    } catch (err) {
        alert('Failed to delete entry');
    }
}

// Edit form submission
document.getElementById('edit-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    
    const id = document.getElementById('edit-entry-id').value;
    const content = document.getElementById('edit-content').value;
    const confidence = parseFloat(document.getElementById('edit-confidence').value);
    const tags = document.getElementById('edit-tags').value.split(',').map(t => t.trim()).filter(Boolean);
    
    try {
        await fetch(API.entry(id), {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ content, confidence, tags }),
        });
        
        document.getElementById('edit-modal').style.display = 'none';
        loadEntries();
    } catch (err) {
        alert('Failed to update entry');
    }
});

// Search
document.getElementById('btn-search').addEventListener('click', async () => {
    const query = document.getElementById('search-input').value;
    const type = document.getElementById('search-type').value;
    
    if (!query) return;
    
    try {
        const params = new URLSearchParams({ q: query, semantic: type === 'semantic' });
        const res = await fetch(`${API.search}?${params}`);
        const data = await res.json();
        
        const container = document.getElementById('search-results');
        if (!data.results || data.results.length === 0) {
            container.innerHTML = '<p class="empty-state">No results found</p>';
            return;
        }
        
        container.innerHTML = data.results.map(entry => `
            <div class="result-item">
                <h4><span class="type-badge ${entry.entry_type}">${entry.entry_type}</span></h4>
                <p>${escapeHtml(entry.content || '').substring(0, 200)}</p>
                <small>Confidence: ${(entry.confidence || 0).toFixed(2)} | ${formatDate(entry.created_at)}</small>
                <br>
                <button onclick="viewEntry('${entry.id}')">View</button>
                <button onclick="editEntry('${entry.id}')">Edit</button>
            </div>
        `).join('');
        
    } catch (err) {
        alert('Search failed');
    }
});

// Sessions
async function loadSessions() {
    try {
        const res = await fetch(API.sessions);
        const data = await res.json();
        
        const container = document.getElementById('sessions-list');
        if (!data.sessions || data.sessions.length === 0) {
            container.innerHTML = '<p class="empty-state">No sessions found</p>';
            return;
        }
        
        container.innerHTML = data.sessions.map(session => `
            <div class="session-item" onclick="viewSession('${session.id}')">
                <h4>${session.name || 'Unnamed Session'}</h4>
                <p>${session.summary || 'No summary'}</p>
                <small>Quality: ${(session.quality_score || 0).toFixed(2)} | ${formatDate(session.updated_at)}</small>
            </div>
        `).join('');
        
    } catch (err) {
        console.error('Failed to load sessions:', err);
    }
}

async function viewSession(id) {
    try {
        const res = await fetch(API.session(id));
        const data = await res.json();
        
        // For now, just show summary
        alert(`Session: ${id}\n\nSummary: ${data.summary?.summary || 'No summary'}\n\nTags: ${data.tags?.join(', ') || 'None'}`);
    } catch (err) {
        alert('Failed to load session');
    }
}

// Analytics
async function loadAnalytics() {
    try {
        const [toolsRes, llmRes] = await Promise.all([
            fetch(API.analyticsTools),
            fetch(API.analyticsLlm),
        ]);
        
        const tools = await toolsRes.json();
        const llm = await llmRes.json();
        
        // Tool usage chart
        const toolChart = document.getElementById('tool-usage-chart');
        const toolEntries = Object.entries(tools);
        if (toolEntries.length === 0) {
            toolChart.innerHTML = '<p class="empty-state">No tool usage data</p>';
        } else {
            const maxToolCalls = Math.max(...toolEntries.map(([, v]) => v.total_calls || 0), 1);
            toolChart.innerHTML = toolEntries.map(([name, stats]) => {
                const width = ((stats.total_calls || 0) / maxToolCalls) * 100;
                return `
                    <div class="chart-bar">
                        <span class="chart-label">${name}</span>
                        <div class="chart-value" style="width: ${Math.max(width, 5)}%">${stats.total_calls || 0}</div>
                    </div>
                `;
            }).join('');
        }
        
        // LLM usage chart
        const llmChart = document.getElementById('llm-usage-chart');
        const llmEntries = Object.entries(llm);
        if (llmEntries.length === 0) {
            llmChart.innerHTML = '<p class="empty-state">No LLM usage data</p>';
        } else {
            llmChart.innerHTML = llmEntries.map(([model, stats]) => `
                <div class="chart-bar">
                    <span class="chart-label">${model}</span>
                    <div class="chart-value" style="width: 80%">${stats.total_calls || 0} calls</div>
                </div>
            `).join('');
        }
        
    } catch (err) {
        console.error('Failed to load analytics:', err);
    }
}

// PII Scan
document.getElementById('btn-scan-pii').addEventListener('click', async () => {
    const container = document.getElementById('pii-results');
    container.innerHTML = '<p>Scanning...</p>';
    
    try {
        const res = await fetch(API.piiScan);
        const data = await res.json();
        
        if (!data.flagged || data.flagged.length === 0) {
            container.innerHTML = `<p>No PII found in ${data.total_scanned} entries.</p>`;
            return;
        }
        
        container.innerHTML = `
            <p>Found ${data.flagged.length} potential PII items in ${data.total_scanned} entries:</p>
            ${data.flagged.map(item => `
                <div class="pii-item">
                    <div class="pii-type">${item.pii_type.toUpperCase()}</div>
                    <div class="pii-matches">Matches: ${item.matches.join(', ')}</div>
                    <p>${escapeHtml(item.content_preview).substring(0, 150)}...</p>
                    <button onclick="redactPII('${item.entry_id}', '${item.pii_type}')">Redact This</button>
                    <button onclick="redactPII('${item.entry_id}')">Redact All PII</button>
                    <button onclick="deleteEntry('${item.entry_id}')" class="btn-danger">Delete Entry</button>
                </div>
            `).join('')}
        `;
        
    } catch (err) {
        container.innerHTML = '<p class="error">Scan failed</p>';
    }
});

async function redactPII(entryId, piiType) {
    if (!confirm('Redact PII from this entry?')) return;
    
    try {
        await fetch(API.piiRedact, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ entry_id: entryId, pii_type: piiType }),
        });
        
        document.getElementById('btn-scan-pii').click();
    } catch (err) {
        alert('Redaction failed');
    }
}

// Settings
async function loadSettings() {
    try {
        const res = await fetch(API.settings);
        const settings = await res.json();
        
        document.getElementById('setting-auto-skills').checked = settings.auto_create_skills;
        document.getElementById('setting-auto-tools').checked = settings.auto_create_tools;
        document.getElementById('setting-max-creations').value = settings.max_auto_creations;
        document.getElementById('setting-min-confidence').value = settings.min_confidence;
        
    } catch (err) {
        console.error('Failed to load settings:', err);
    }
}

document.getElementById('btn-save-settings').addEventListener('click', async () => {
    const settings = {
        auto_create_skills: document.getElementById('setting-auto-skills').checked,
        auto_create_tools: document.getElementById('setting-auto-tools').checked,
        max_auto_creations: parseInt(document.getElementById('setting-max-creations').value),
        min_confidence: parseFloat(document.getElementById('setting-min-confidence').value),
    };
    
    try {
        await fetch(API.settings, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(settings),
        });
        
        alert('Settings saved (runtime only - restart to apply permanently)');
    } catch (err) {
        alert('Failed to save settings');
    }
});

// Export
document.getElementById('btn-export-json').addEventListener('click', () => exportKB('json'));
document.getElementById('btn-export-csv').addEventListener('click', () => exportKB('csv'));

async function exportKB(format) {
    try {
        const res = await fetch(API.export, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ format }),
        });
        
        const data = await res.json();
        
        // Download file
        const blob = new Blob([data.data], { type: format === 'csv' ? 'text/csv' : 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = data.filename;
        a.click();
        URL.revokeObjectURL(url);
        
    } catch (err) {
        alert('Export failed');
    }
}

// Import
document.getElementById('btn-import').addEventListener('click', async () => {
    const dataStr = document.getElementById('import-data').value;
    if (!dataStr) {
        alert('Please paste JSON data first');
        return;
    }
    
    try {
        const data = JSON.parse(dataStr);
        const res = await fetch(API.import, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ data: Array.isArray(data) ? data : [data] }),
        });
        
        const result = await res.json();
        alert(`Imported ${result.imported} entries`);
        document.getElementById('import-data').value = '';
        
    } catch (err) {
        alert('Import failed: ' + err.message);
    }
});

// Clear KB
document.getElementById('btn-clear-kb').addEventListener('click', async () => {
    if (!confirm('WARNING: This will delete ALL knowledge base entries. This cannot be undone!')) return;
    if (!confirm('Are you absolutely sure? Type "CLEAR" in your mind and click OK.')) return;
    
    try {
        await fetch(API.clear, {
            method: 'DELETE',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ confirm: true }),
        });
        
        alert('Knowledge base cleared');
        loadDashboard();
        
    } catch (err) {
        alert('Clear failed');
    }
});

// Bulk delete
document.getElementById('btn-bulk-delete').addEventListener('click', async () => {
    const checkboxes = document.querySelectorAll('.entry-checkbox:checked');
    const ids = Array.from(checkboxes).map(cb => cb.dataset.id);
    
    if (ids.length === 0) return;
    if (!confirm(`Delete ${ids.length} entries?`)) return;
    
    try {
        await fetch(`${API.entries}/bulk-delete`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ entry_ids: ids }),
        });
        
        loadEntries();
        
    } catch (err) {
        alert('Bulk delete failed');
    }
});

// Select all checkbox
document.getElementById('select-all')?.addEventListener('change', (e) => {
    document.querySelectorAll('.entry-checkbox').forEach(cb => {
        cb.checked = e.target.checked;
    });
    document.getElementById('btn-bulk-delete').style.display = 
        e.target.checked ? 'inline-block' : 'none';
});

// Filter buttons
document.getElementById('btn-apply-filters')?.addEventListener('click', () => {
    entriesOffset = 0;
    loadEntries();
});

// Modal close
document.querySelectorAll('.close').forEach(btn => {
    btn.addEventListener('click', () => {
        btn.closest('.modal').style.display = 'none';
    });
});

// Helpers
function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

function formatDate(isoStr) {
    if (!isoStr) return '-';
    try {
        const d = new Date(isoStr);
        return d.toLocaleDateString() + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    } catch {
        return isoStr;
    }
}

// Init
document.addEventListener('DOMContentLoaded', () => {
    // Check hash for initial page
    const hash = window.location.hash.slice(1);
    if (hash && document.getElementById(`page-${hash}`)) {
        navigateTo(hash);
    } else {
        loadDashboard();
    }
});
