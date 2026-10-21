'use strict';
// Regression coverage for the tool-manager XSS fixes.
//
// Two problems:
//  1. escapeHtml was DOM-based (textContent -> innerHTML), so it encoded & < >
//     but left " and ' untouched. It was interpolated into inline
//     onclick="viewTool('...')" strings, where the browser HTML-decodes the
//     attribute before the JS parses it -- so an apostrophe in a tool name
//     (arbitrary custom-tool metadata) closed the string and injected code.
//  2. Scan findings rendered result.risk_level, f.pattern and f.matches without
//     escaping; those come straight from custom-tool source.

const test = require('node:test');
const assert = require('node:assert');
const { bootTools } = require('./harness.js');

const NASTY = `evil');alert(1);//`;
const NASTY_ATTR = `x"><img src=x onerror=alert(1)>`;

test('a quote in a tool name does not break out of the row markup', async () => {
  const { document: doc, errors } = await bootTools({
    fixtures: {
      '/api/tools/manage/list': {
        tools: [
          { name: NASTY, type: 'custom', description: 'd' },
          { name: NASTY_ATTR, type: 'builtin', description: 'd' },
        ],
      },
    },
  });
  assert.deepStrictEqual(errors, []);

  // No injected element/attribute anywhere in the rendered list.
  assert.strictEqual(doc.querySelector('#tools-list img'), null, 'no injected <img>');
  assert.strictEqual(
    doc.querySelectorAll('#tools-list [onerror], #tools-list [onmouseover], #tools-list [onclick]').length,
    0,
    'no inline event-handler attributes are produced from data',
  );

  // The names survive intact on the delegated data-* buttons.
  const names = Array.from(doc.querySelectorAll('#tools-list [data-action="view"]'))
    .map((b) => b.dataset.name);
  assert.ok(names.includes(NASTY), 'the apostrophe name is preserved literally');
  assert.ok(names.includes(NASTY_ATTR), 'the attribute-breakout name is preserved literally');
});

test('delegated click handlers receive the literal name (not injected code)', async () => {
  const { window, document: doc } = await bootTools({
    fixtures: {
      '/api/tools/manage/list': { tools: [{ name: NASTY, type: 'custom', trusted: true, description: 'd' }] },
    },
  });

  // deleteTool calls confirm(); capture its argument to prove the literal name
  // flows through delegation rather than being parsed as code.
  let confirmArg = null;
  window.confirm = (msg) => { confirmArg = msg; return false; };

  const delBtn = doc.querySelector('#tools-list [data-action="delete"]');
  assert.strictEqual(delBtn.dataset.name, NASTY, 'delete button carries the literal name');
  delBtn.dispatchEvent(new window.MouseEvent('click', { bubbles: true, cancelable: true }));
  assert.ok(confirmArg.includes(NASTY), 'confirm received the literal name');

  // toggle-trust reads data-name and data-trusted; both must be the literal values.
  const trustBtn = doc.querySelector('#tools-list [data-action="toggle-trust"]');
  assert.strictEqual(trustBtn.dataset.name, NASTY);
  assert.strictEqual(trustBtn.dataset.trusted, '1');
});

test('scan findings are escaped', async () => {
  const { window, document: doc } = await bootTools({
    fixtures: {
      '/api/tools/manage/scan': {
        results: [
          {
            tool_name: NASTY_ATTR,
            risk_level: 'high',
            findings: [{ pattern: '<script>', matches: [`" onmouseover="alert(1)`] }],
          },
        ],
      },
    },
  });

  doc.getElementById('btn-scan').dispatchEvent(new window.MouseEvent('click', { bubbles: true, cancelable: true }));
  await new Promise((r) => setTimeout(r, 30));

  const container = doc.getElementById('scan-results');
  assert.strictEqual(container.querySelector('script'), null, 'no injected <script>');
  assert.strictEqual(
    container.querySelectorAll('[onmouseover]').length,
    0,
    'no attribute breakout from scan matches',
  );
  // The literal pattern text is present as text, not markup.
  assert.ok(container.textContent.includes('<script>'), 'finding text is rendered escaped');
});
