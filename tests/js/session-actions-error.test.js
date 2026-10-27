'use strict';
// Regression coverage for §9: createSession()/deleteSession() called api() from
// click handlers with no try/except, so a failed fetch (server 500, network
// blip) rejected unhandled in the handler -- no feedback to the user, just a
// console error. They now wrap their body in try/except and surface failures via
// showError().

const test = require('node:test');
const assert = require('node:assert');
const { boot } = require('./harness.js');

const systemErrors = (env) =>
  Array.from(env.document.querySelectorAll('#messages .system-error'));

// A real unhandled rejection vs. the harness's benign "no WebSocket created"
// note (which only fires at boot if connectWS never ran -- not our case here).
const realRejections = (env) =>
  env.errors.filter((m) => !/no WebSocket was created by the boot sequence/.test(m));

const okResp = (body) => ({
  ok: true,
  status: 200,
  json: async () => body,
  text: async () => JSON.stringify(body),
});

test('a failed createSession shows a system error instead of rejecting', async () => {
  const env = await boot();
  assert.strictEqual(systemErrors(env).length, 0, 'a clean boot shows no error');

  // POST /api/sessions fails; keep the GETs the harness may still hit well-behaved.
  env.window.fetch = async (url, opts) => {
    if (opts && opts.method === 'POST') throw new Error('server 500');
    return okResp({});
  };

  // The fix: this resolves (does not reject) and surfaces the failure.
  await env.window.createSession();

  const errs = systemErrors(env);
  assert.strictEqual(errs.length, 1, 'the create failure is surfaced');
  assert.match(errs[0].textContent, /server 500/, 'the failure reason is shown');
  assert.strictEqual(
    realRejections(env).length,
    0,
    'the rejection is handled, not left unhandled',
  );
});

test('a failed deleteSession shows a system error instead of rejecting', async () => {
  const env = await boot();
  env.window.confirm = () => true; // get past the confirm dialog

  env.window.fetch = async (url, opts) => {
    if (opts && opts.method === 'DELETE') throw new Error('delete failed');
    return okResp({});
  };

  await env.window.deleteSession('session-1');

  const errs = systemErrors(env);
  assert.strictEqual(errs.length, 1, 'the delete failure is surfaced');
  assert.match(errs[0].textContent, /delete failed/, 'the failure reason is shown');
  assert.strictEqual(
    realRejections(env).length,
    0,
    'the rejection is handled, not left unhandled',
  );
});

test('a successful createSession shows no system error (control)', async () => {
  const env = await boot();

  env.window.fetch = async (url, opts) => {
    const u = String(url);
    if (opts && opts.method === 'POST' && u.includes('/api/sessions')) {
      return okResp({ id: 'session-new' });
    }
    if (opts && opts.method === 'GET' && u.includes('/api/sessions') && !u.includes('/messages')) {
      return okResp([]);
    }
    return okResp([]);
  };

  await env.window.createSession();

  assert.strictEqual(
    systemErrors(env).length,
    0,
    'a successful create shows no error',
  );
});
