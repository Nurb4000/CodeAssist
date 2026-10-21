// Tool Manager JavaScript

const API = {
    list: '/api/tools/manage/list',
    details: (name) => `/api/tools/manage/${name}`,
    trust: (name) => `/api/tools/manage/${name}/trust`,
    delete: (name) => `/api/tools/manage/${name}`,
    usage: '/api/tools/manage/usage',
    scan: '/api/tools/manage/scan',
};

// State
let currentPage = 'all';
let allTools = [];

/* Alphabetical by name, case-insensitively. Named rather than an inline arrow so
 * it can be reused by both list views. */
function byName(a, b) {
    return String(a && a.name || '').localeCompare(String(b && b.name || ''));
}

// Navigation
document.querySelectorAll('.nav-item').forEach(item => {
    item.addEventListener('click', (e) => {
        e.preventDefault();
        const page = item.dataset.page;
        navigateTo(page);
    });
});

// Row actions are wired by delegation off data-* attributes, NOT inline
// onclick="viewTool('${name}')" strings. Entity-escaping a name into a JS string
// literal inside an HTML attribute does not work: the browser HTML-decodes the
// attribute before the JS is parsed, so an apostrophe in a tool name (which
// comes from arbitrary custom-tool metadata) closed the string and injected code.
document.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-action]');
    if (!btn) return;
    const name = btn.dataset.name;
    switch (btn.dataset.action) {
        case 'view': viewTool(name); break;
        case 'delete': deleteTool(name); break;
        case 'toggle-trust': toggleTrust(name, btn.dataset.trusted === '1'); break;
        case 'set-trust': setTrust(name, btn.checked); break;
    }
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
        case 'all':
            await loadAllTools();
            break;
        case 'custom':
            await loadCustomTools();
            break;
        case 'usage':
            await loadUsageStats();
            break;
    }
}

// Load all tools
async function loadAllTools() {
    try {
        const res = await fetch(API.list);
        const data = await res.json();
        allTools = (data.tools || []).sort(byName);
        
        renderToolsList(allTools);
        
    } catch (err) {
        console.error('Failed to load tools:', err);
    }
}

function renderToolsList(tools) {
    const container = document.getElementById('tools-list');
    
    if (!tools || tools.length === 0) {
        container.innerHTML = '<p class="empty-state">No tools found</p>';
        return;
    }
    
    container.innerHTML = tools.map(tool => `
        <div class="tool-card" data-type="${escapeHtml(tool.type)}">
            <div class="tool-icon ${escapeHtml(tool.type)}">${tool.type === 'builtin' ? '🔧' : '🛠️'}</div>
            <div class="tool-info">
                <div class="tool-name">
                    ${escapeHtml(tool.name)}
                    <span class="tool-type-badge ${escapeHtml(tool.type)}">${escapeHtml(tool.type)}</span>
                    <span class="trust-badge ${tool.trusted ? 'trusted' : 'untrusted'}">
                        ${tool.trusted ? 'Trusted' : 'Untrusted'}
                    </span>
                </div>
                <div class="tool-description">${escapeHtml(tool.description || 'No description')}</div>
                <div class="tool-meta">
                    <span>Used: ${tool.usage_count || 0} times</span>
                    <span>Success: ${tool.success_rate || 0}%</span>
                </div>
            </div>
            <div class="tool-actions">
                <button data-action="view" data-name="${escapeHtml(tool.name)}">View</button>
                ${tool.type === 'custom' ? `
                    <button data-action="toggle-trust" data-name="${escapeHtml(tool.name)}" data-trusted="${tool.trusted ? '1' : '0'}">
                        ${tool.trusted ? 'Untrust' : 'Trust'}
                    </button>
                    <button data-action="delete" data-name="${escapeHtml(tool.name)}" class="btn-danger">Delete</button>
                ` : ''}
            </div>
        </div>
    `).join('');
}

// Load custom tools only
async function loadCustomTools() {
    try {
        const res = await fetch(API.list);
        const data = await res.json();
        const customTools = (data.tools || []).filter(t => t.type === 'custom').sort(byName);
        
        const container = document.getElementById('custom-tools-list');
        
        if (customTools.length === 0) {
            container.innerHTML = '<p class="empty-state">No custom tools found.<br>Create tools using the create_tool tool in chat.</p>';
            return;
        }
        
        container.innerHTML = customTools.map(tool => `
            <div class="tool-card">
                <div class="tool-icon custom">🛠️</div>
                <div class="tool-info">
                    <div class="tool-name">
                        ${escapeHtml(tool.name)}
                        <span class="trust-badge ${tool.trusted ? 'trusted' : 'untrusted'}">
                            ${tool.trusted ? 'Trusted' : 'Untrusted'}
                        </span>
                    </div>
                    <div class="tool-description">${escapeHtml(tool.description || 'No description')}</div>
                    <div class="tool-meta">
                        <span>Source: ${escapeHtml(tool.source || 'Unknown')}</span>
                        <span>Used: ${tool.usage_count || 0} times</span>
                    </div>
                </div>
                <div class="tool-actions">
                    <button data-action="view" data-name="${escapeHtml(tool.name)}">View Code</button>
                    <label class="trust-toggle">
                        <input type="checkbox" ${tool.trusted ? 'checked' : ''}
                               data-action="set-trust" data-name="${escapeHtml(tool.name)}">
                        <span class="trust-slider"></span>
                    </label>
                    <button data-action="delete" data-name="${escapeHtml(tool.name)}" class="btn-danger">Delete</button>
                </div>
            </div>
        `).join('');
        
    } catch (err) {
        console.error('Failed to load custom tools:', err);
    }
}

// View tool details
async function viewTool(name) {
    try {
        const res = await fetch(API.details(name));
        const tool = await res.json();
        
        const content = document.getElementById('modal-tool-content');
        content.innerHTML = `
            <p><strong>Type:</strong> <span class="tool-type-badge ${tool.type}">${tool.type}</span></p>
            <p><strong>Trust Level:</strong> <span class="trust-badge ${tool.trusted ? 'trusted' : 'untrusted'}">
                ${tool.trusted ? 'Trusted' : 'Untrusted'}
            </span></p>
            <p><strong>Description:</strong></p>
            <p>${escapeHtml(tool.description || 'No description')}</p>
            
            ${tool.schema ? `
                <p><strong>Parameters Schema:</strong></p>
                <div class="code-preview">${escapeHtml(JSON.stringify(tool.schema, null, 2))}</div>
            ` : ''}
            
            ${tool.source_code ? `
                <p><strong>Source Code:</strong></p>
                <div class="code-preview">${escapeHtml(tool.source_code)}</div>
                <p><strong>Location:</strong> ${escapeHtml(tool.source_path)}</p>
            ` : ''}
        `;
        
        document.getElementById('modal-tool-name').textContent = `Tool: ${name}`;
        document.getElementById('tool-modal').style.display = 'flex';
        
    } catch (err) {
        alert('Failed to load tool details');
    }
}

// Toggle trust level
async function toggleTrust(name, currentTrusted) {
    await setTrust(name, !currentTrusted);
}

async function setTrust(name, trusted) {
    try {
        await fetch(API.trust(name), {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ trusted }),
        });
        
        // Reload current page
        loadPage(currentPage);
        
    } catch (err) {
        alert('Failed to update trust level');
    }
}

// Delete custom tool
async function deleteTool(name) {
    if (!confirm(`Delete tool "${name}"? This cannot be undone.`)) return;
    
    try {
        await fetch(API.delete(name), { method: 'DELETE' });
        loadPage(currentPage);
        
    } catch (err) {
        alert('Failed to delete tool');
    }
}

// Security scan
document.getElementById('btn-scan')?.addEventListener('click', async () => {
    const container = document.getElementById('scan-results');
    container.innerHTML = '<p>Scanning...</p>';
    
    try {
        const res = await fetch(API.scan);
        const data = await res.json();
        
        if (!data.results || data.results.length === 0) {
            container.innerHTML = '<p>No custom tools to scan.</p>';
            return;
        }
        
        container.innerHTML = data.results.map(result => `
            <div class="scan-item">
                <div class="scan-header">
                    <strong>${escapeHtml(result.tool_name)}</strong>
                    <span class="risk-badge ${escapeHtml(result.risk_level)}">${escapeHtml(String(result.risk_level).toUpperCase())} RISK</span>
                </div>
                ${result.findings.length > 0 ? `
                    <div class="scan-findings">
                        ${result.findings.map(f => `
                            <div class="scan-finding">
                                <span class="scan-finding-type">${escapeHtml(f.pattern)}:</span>
                                <span class="scan-finding-matches">${escapeHtml((f.matches || []).join(', '))}</span>
                            </div>
                        `).join('')}
                    </div>
                ` : '<p>No dangerous patterns found</p>'}
                <div style="margin-top: 10px;">
                    <button data-action="view" data-name="${escapeHtml(result.tool_name)}">View Code</button>
                    <button data-action="delete" data-name="${escapeHtml(result.tool_name)}" class="btn-danger">Delete</button>
                </div>
            </div>
        `).join('');
        
    } catch (err) {
        container.innerHTML = '<p class="error">Scan failed</p>';
    }
});

// Usage stats
async function loadUsageStats() {
    try {
        const res = await fetch(API.usage);
        const data = await res.json();
        
        const container = document.getElementById('usage-stats');
        const tools = data.tools || {};
        const entries = Object.entries(tools);
        
        if (entries.length === 0) {
            container.innerHTML = '<p class="empty-state">No usage data available yet.</p>';
            return;
        }
        
        const maxCalls = Math.max(...entries.map(([, v]) => v.total_calls || 0), 1);
        
        container.innerHTML = `
            <p>Total calls: <strong>${data.total_calls || 0}</strong> (last ${data.period_days} days)</p>
            ${entries.map(([name, stats]) => {
                const width = ((stats.total_calls || 0) / maxCalls) * 100;
                return `
                    <div class="usage-stat-card">
                        <div class="usage-stat-header">
                            <span class="usage-stat-name">${escapeHtml(name)}</span>
                            <span class="usage-stat-count">${stats.total_calls || 0}</span>
                        </div>
                        <div class="tool-meta">
                            <span>Successful: ${stats.successful || 0}</span>
                            <span>Failed: ${stats.failed || 0}</span>
                            <span>Avg duration: ${Math.round(stats.avg_duration_ms || 0)}ms</span>
                        </div>
                        <div class="usage-stat-bar">
                            <div class="usage-stat-fill" style="width: ${width}%"></div>
                        </div>
                    </div>
                `;
            }).join('')}
        `;
        
    } catch (err) {
        console.error('Failed to load usage stats:', err);
    }
}

// Filter tools
document.getElementById('btn-apply-filters')?.addEventListener('click', () => {
    const typeFilter = document.getElementById('filter-type').value;
    const searchFilter = document.getElementById('filter-search').value.toLowerCase();
    
    let filtered = allTools;
    
    if (typeFilter) {
        filtered = filtered.filter(t => t.type === typeFilter);
    }
    
    if (searchFilter) {
        filtered = filtered.filter(t => 
            t.name.toLowerCase().includes(searchFilter) ||
            (t.description || '').toLowerCase().includes(searchFilter)
        );
    }
    
    renderToolsList(filtered);
});

// Modal close
document.querySelectorAll('.close').forEach(btn => {
    btn.addEventListener('click', () => {
        btn.closest('.modal').style.display = 'none';
    });
});

// Helpers
function escapeHtml(s) {
    // Escapes quotes as well as & < >. The previous DOM-based helper (textContent
    // -> innerHTML) left " and ' untouched, so it was unsafe in attribute and
    // inline-handler context: this helper is interpolated into single-quoted
    // onclick="viewTool('...')" strings built from tool names.
    return String(s ?? '').replace(/[&<>"']/g, (c) => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
}

// Init
document.addEventListener('DOMContentLoaded', () => {
    loadAllTools();
});
