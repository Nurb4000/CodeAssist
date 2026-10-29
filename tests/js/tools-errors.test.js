'use strict';
// Regression for a §10 GAP: tool-manager failures were silent (only
// console.error) or surfaced via alert(), with no res.ok checks, so a failed
// load left the page blank with no feedback and a non-2xx response was parsed
// as if it were success. Every failure path now routes through showToolError(),
// which renders an in-page banner (#tool-error); clearToolError() runs at the
// start of each load/action so the banner clears once the next operation
// succeeds.

const test = require('node:test');
const assert = require('node:assert');
const { bootTools } = require('./harness.js');

const banner = (env) => env.document.getElementById('tool-error');

test('a non-2xx tool load shows an in-page error instead of failing silently', async () => {
  const env = await bootTools({ fixtures: { '/api/tools/manage/list': { tools: [] } } });
  // A server error on reload: ok:false, and a body that is not valid tool JSON.
  env.window.fetch = async () => ({ ok: false, status: 500, json: async () => ({ message: 'boom' }) });

  await env.window.loadAllTools();

  assert.strictEqual(banner(env).style.display, 'block', 'the error banner is shown');
  assert.match(banner(env).textContent, /HTTP 500/, 'the status is surfaced to the user');
});

test('a throwing fetch (network error) is surfaced, not swallowed', async () => {
  const env = await bootTools({ fixtures: { '/api/tools/manage/list': { tools: [] } } });
  env.window.fetch = async () => { throw new Error('connection refused'); };

  await env.window.loadCustomTools();

  assert.strictEqual(banner(env).style.display, 'block');
  assert.match(banner(env).textContent, /connection refused/);
});

test('the error banner clears once a subsequent load succeeds', async () => {
  const env = await bootTools({ fixtures: { '/api/tools/manage/list': { tools: [] } } });

  // First a failure...
  env.window.fetch = async () => ({ ok: false, status: 503, json: async () => ({}) });
  await env.window.loadAllTools();
  assert.strictEqual(banner(env).style.display, 'block', 'error shown on failure');

  // ...then a success clears it.
  env.window.fetch = async () => ({ ok: true, status: 200, json: async () => ({ tools: [] }) });
  await env.window.loadAllTools();
  assert.strictEqual(banner(env).style.display, 'none', 'banner cleared on success');
});

test('a failed action (trust) surfaces the error rather than alerting', async () => {
  const env = await bootTools({ fixtures: { '/api/tools/manage/list': { tools: [] } } });
  env.window.fetch = async () => ({ ok: false, status: 403, json: async () => ({}) });

  await env.window.setTrust('some-tool', true);

  assert.strictEqual(banner(env).style.display, 'block');
  assert.match(banner(env).textContent, /HTTP 403/);
});
