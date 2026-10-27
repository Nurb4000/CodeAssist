'use strict';
// Regression coverage for §9: `loadMessages()` had no error handling, so a
// failed /messages fetch (session deleted mid-load, server 500, network blip)
// rejected unhandled -- leaving the session blank with no feedback, and on the
// boot path (the async IIFE at the bottom of app.js) as an unhandled rejection.
// It now surfaces the failure via showError() and bails instead of throwing.

const test = require('node:test');
const assert = require('node:assert');
const { boot } = require('./harness.js');

const systemErrors = (env) =>
  Array.from(env.document.querySelectorAll('#messages .system-error'));

test('a failed fetch is caught and shown, not thrown', async () => {
  const env = await boot(); // clean boot with the default fixtures

  // Make only the /messages endpoint fail on the next load.
  env.window.fetch = async (url) => {
    if (String(url).includes('/messages')) throw new Error('network down');
    return { ok: true, status: 200, json: async () => ({}) };
  };

  // The fix: loadMessages() resolves (does not reject) and surfaces the failure.
  await env.window.loadMessages();

  const errs = systemErrors(env);
  assert.strictEqual(errs.length, 1, 'exactly one system error is shown');
  assert.match(errs[0].textContent, /network down/, 'the failure reason is surfaced');
});

test('an HTTP error body surfaces through the same path', async () => {
  const env = await boot();

  env.window.fetch = async (url) => {
    if (String(url).includes('/messages')) {
      return { ok: false, status: 500, json: async () => ({ detail: 'backend down' }) };
    }
    return { ok: true, status: 200, json: async () => ({}) };
  };

  await env.window.loadMessages();

  const errs = systemErrors(env);
  assert.strictEqual(errs.length, 1, 'the 500 is surfaced, not swallowed');
  assert.match(errs[0].textContent, /backend down/, 'the server detail is shown');
});

test('a successful load shows no system error', async () => {
  const env = await boot(); // default fixture: session-1 messages = []
  await env.window.loadMessages();
  assert.strictEqual(
    systemErrors(env).length,
    0,
    'no error is shown when the load succeeds',
  );
});
