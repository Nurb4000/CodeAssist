'use strict';
// Automation completion frames arrive while the UI may be running another turn.
// They render as their own notification row: no busy/disconnect state changes,
// and untrusted job names/output must be HTML-escaped.

const test = require('node:test');
const assert = require('node:assert');
const { boot, startTurn } = require('./harness.js');

test('automation_result renders a notification without ending an active turn', async () => {
  const env = await boot();
  startTurn(env);
  assert.ok(env.document.body.classList.contains('is-working'), 'precondition: turn running');

  env.feed({
    type: 'automation_result',
    name: 'nightly <script>',
    status: 'ok',
    output: 'summary <b>text</b>',
  });

  const rows = env.document.querySelectorAll('.automation-result');
  assert.strictEqual(rows.length, 1, 'one notification row added');
  assert.match(rows[0].textContent, /Automation · nightly <script>/);
  assert.strictEqual(rows[0].querySelector('script'), null, 'job name is escaped');
  assert.strictEqual(rows[0].querySelector('b'), null, 'job output is escaped');
  assert.ok(env.document.body.classList.contains('is-working'), 'concurrent turn stays busy');
});

test('failed automation_result is visibly distinct', async () => {
  const env = await boot();
  env.feed({ type: 'automation_result', name: 'broken', status: 'error', error: 'boom' });
  const row = env.document.querySelector('.automation-result');
  assert.ok(row.classList.contains('automation-error'));
  assert.match(row.textContent, /boom/);
  assert.ok(!env.document.body.classList.contains('is-working'));
});
