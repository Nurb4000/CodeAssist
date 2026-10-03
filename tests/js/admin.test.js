'use strict';
// Behavioural tests for the admin (registry) page.
//
// Regression coverage for: a SyntaxError in admin.js killed the whole script, so
// the page fell back to its static markup. That surfaced as a scatter of
// unrelated-looking symptoms -- only some sections collapsible, the sidebar links
// doing nothing, the skills and agents tables empty, and Settings reduced to its
// Save row (everything else in that section is built by JS). A syntax check
// guards the parse failure (tests/test_static_assets.py); these tests guard the
// script actually doing its job when it does parse.

const test = require('node:test');
const assert = require('node:assert');
const { bootAdmin } = require('./harness.js');

const SECTION_IDS = [
  'health',
  'skills',
  'mcp',
  'lsp',
  'plugins',
  'custom-tools',
  'agents',
  'settings',
];

const sections = (doc) => Array.from(doc.querySelectorAll('.admin-section'));
const cellTexts = (doc, tbodyId) =>
  Array.from(doc.querySelectorAll(`#${tbodyId} tr td.cell-em`)).map((n) => n.textContent.trim());

test('admin.js runs without error', async () => {
  const { errors } = await bootAdmin();
  assert.deepStrictEqual(errors, []);
});

test('every nav entry is a collapsible section', async () => {
  const { document: doc } = await bootAdmin();

  assert.deepStrictEqual(
    sections(doc).map((s) => s.dataset.section),
    SECTION_IDS,
    'each admin-nav link should own exactly one collapsible section',
  );
  // A section that was never wrapped is still a bare heading, so the count of
  // headings carrying the chevron is the real assertion.
  assert.strictEqual(doc.querySelectorAll('.section-toggle').length, SECTION_IDS.length);
  assert.strictEqual(
    doc.querySelectorAll('#chat-area > h2[id]').length,
    0,
    'no heading should be left unwrapped',
  );
});

test('sections start collapsed and the chevron expands them', async () => {
  const { window, document: doc } = await bootAdmin();

  const skills = doc.querySelector('.admin-section[data-section="skills"]');
  assert.ok(skills.classList.contains('collapsed'), 'sections default to collapsed');

  const toggle = skills.querySelector('.section-toggle');
  assert.strictEqual(toggle.getAttribute('aria-expanded'), 'false');

  toggle.dispatchEvent(new window.MouseEvent('click', { bubbles: true, cancelable: true }));
  assert.ok(!skills.classList.contains('collapsed'), 'chevron expands the section');
  assert.strictEqual(toggle.getAttribute('aria-expanded'), 'true');

  toggle.dispatchEvent(new window.MouseEvent('click', { bubbles: true, cancelable: true }));
  assert.ok(skills.classList.contains('collapsed'), 'chevron collapses it again');
});

test('clicking a sidebar link expands that section', async () => {
  const { window, document: doc } = await bootAdmin();

  const link = doc.querySelector('.admin-nav a[href="#settings"]');
  const settings = doc.querySelector('.admin-section[data-section="settings"]');
  assert.ok(settings.classList.contains('collapsed'));

  link.dispatchEvent(new window.MouseEvent('click', { bubbles: true, cancelable: true }));
  assert.ok(
    !settings.classList.contains('collapsed'),
    'navigating to a section must open it',
  );
});

test('skills and custom tools are listed alphabetically', async () => {
  const { document: doc } = await bootAdmin({
    fixtures: {
      '/api/skills': {
        skills: [
          { name: 'zebra', description: 'z', category: 'custom' },
          { name: 'Alpha', description: 'a', category: 'custom' },
          { name: 'mid', description: 'm', category: 'custom' },
        ],
      },
      '/api/custom-tools': { tools: [{ name: 'zeta' }, { name: 'beta' }] },
    },
  });

  assert.deepStrictEqual(cellTexts(doc, 'skills-body'), ['Alpha', 'mid', 'zebra']);
  assert.deepStrictEqual(cellTexts(doc, 'custom-body'), ['beta', 'zeta']);
});

test('only custom skills offer edit and delete', async () => {
  // Regression cover: every skill row used to carry an Edit and a Delete button
  // labelled just "skill", including the base skills that ship with the app. The
  // delete silently removed a shipped file and the label did not say what it
  // deleted. Only custom skills are the user's to change.
  const { document: doc } = await bootAdmin({
    fixtures: {
      '/api/skills': {
        skills: [
          { name: 'code-review', description: 'shipped', category: 'base', source: 'codeassist/skills/code-review.md' },
          { name: 'packaged', description: 'from the wheel', category: 'packaged', source: 'package:/x/skills/packaged.md' },
          { name: 'mine', description: 'my own', category: 'custom', source: 'runtime/skills/mine.md' },
          { name: 'mystery', description: 'no category at all' },
        ],
      },
    },
  });

  const actionsBySkill = new Map(
    Array.from(doc.querySelectorAll('#skills-body tr')).map((tr) => [
      tr.querySelector('td.cell-em').textContent.trim(),
      tr.lastElementChild.textContent.trim(),
    ]),
  );

  assert.deepStrictEqual(
    Array.from(actionsBySkill, ([name, actions]) => [name, actions]),
    [
      ['code-review', 'read-only'],
      ['mine', 'EditDelete'],
      ['mystery', 'read-only'],
      ['packaged', 'read-only'],
    ],
  );

  // And no stray button hiding in another column of a non-custom row.
  assert.strictEqual(
    doc.querySelectorAll('#skills-body .admin-btn-danger').length,
    1,
    'exactly one deletable skill',
  );
  assert.strictEqual(
    doc.querySelectorAll('#skills-body .admin-btn-danger')[0].textContent.trim(),
    'Delete',
  );
});

test('row actions get their own column, not the last data cell', async () => {
  // Regression cover: the plugins and custom-tools tables had no actions column,
  // so `tr.lastElementChild` -- the cell the buttons are appended to -- was the
  // Version/Enabled/Description cell. Each row read "1.0yes[plugin]", which looks
  // like the version cell has a control in it. Every table with row actions needs
  // a trailing empty cell of its own, header included.
  const { document: doc } = await bootAdmin({
    fixtures: {
      '/api/skills': { skills: [{ name: 'mine', description: 'd', category: 'custom' }] },
      '/api/plugins': { plugins: [{ name: 'demo', version: '1.0', enabled: true }] },
      '/api/custom-tools': { tools: [{ name: 'mytool', description: 'd' }] },
      '/api/mcp/servers': { servers: [{ id: 'srv', name: 'srv', config: {}, enabled: true }] },
    },
  });

  const tables = ['skills-body', 'mcp-body', 'plugins-body', 'custom-body'];
  for (const id of tables) {
    const table = doc.querySelector(`#${id}`).closest('table');
    const ths = Array.from(table.querySelectorAll('thead th'));
    const row = doc.querySelector(`#${id} tr`);

    assert.strictEqual(
      row.children.length,
      ths.length,
      `#${id}: ${row.children.length} cells against ${ths.length} headers`,
    );
    assert.strictEqual(
      ths[ths.length - 1].textContent.trim(),
      '',
      `#${id}: the last header is the actions column and must be blank`,
    );
    assert.ok(
      row.lastElementChild.querySelector('.admin-btn'),
      `#${id}: the actions column holds the buttons, not a data cell`,
    );
  }

  // And the noun-only danger labels are gone: they read as "plugin" /
  // "custom tool" with no verb, which is what made the skills one confusing.
  for (const id of ['skills-body', 'plugins-body', 'custom-body']) {
    const labels = Array.from(doc.querySelectorAll(`#${id} .admin-btn-danger`))
      .map((b) => b.textContent.trim());
    assert.deepStrictEqual(labels, ['Delete'], `#${id}: delete buttons are labelled`);
  }
});

test('built-in and custom agents are both listed', async () => {
  const { document: doc } = await bootAdmin({
    fixtures: {
      '/api/agents': [
        { id: 'compaction', name: 'compaction', builtin: true },
        { id: 'review', name: 'Review', builtin: true, steps: 8 },
        { id: 'mine', name: 'Mine' },
      ],
    },
  });

  const keys = Array.from(doc.querySelectorAll('#agents-body tr td:first-child'))
    .map((n) => n.textContent.trim());
  assert.deepStrictEqual(keys, ['review', 'mine']);
});

test('the settings section renders its rows, not just the Save button', async () => {
  const { document: doc } = await bootAdmin({
    fixtures: {
      '/api/settings': {
        settings: [
          { key: 'a', label: 'Alpha', group: 'LLM', type: 'str', value: 'x', source: 'file' },
          { key: 'b', label: 'Beta', group: 'LLM', type: 'int', value: 2, source: 'ui' },
          { key: 'c', label: 'Gamma', group: 'Agent', type: 'bool', value: true, source: 'file' },
        ],
      },
    },
  });

  assert.strictEqual(doc.querySelectorAll('#settings-body .settings-group').length, 2);
  assert.strictEqual(
    doc.querySelectorAll('#settings-body tbody tr').length,
    3,
    'all three settings render, not just the static Save row',
  );
  assert.ok(doc.getElementById('set-a'), 'each setting gets an input');
  assert.ok(doc.getElementById('set-b'));
  assert.ok(doc.getElementById('set-c'));
});
