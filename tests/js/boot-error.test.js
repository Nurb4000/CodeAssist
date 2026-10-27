'use strict';
// Regression coverage for the §15.71 follow-up: the boot IIFE at the bottom of
// app.js has several awaits that can reject (loadConfig, /api/sessions,
// loadTodos, loadSessions). Before the fix a failure rejected unhandled there --
// the page went blank with no feedback and a console error. It now wraps the
// whole boot in try/except and surfaces the failure via showError().

const test = require('node:test');
const assert = require('node:assert');
const { boot } = require('./harness.js');

const systemErrors = (env) =>
  Array.from(env.document.querySelectorAll('#messages .system-error'));

// The harness records a benign "no WebSocket was created by the boot sequence"
// note in env.errors whenever boot aborts before connectWS() runs -- which is
// exactly what happens when an early fetch fails. That is expected here; a *real*
// unhandled rejection would be a different message (a stack trace / error text).
const realRejections = (env) =>
  env.errors.filter((m) => !/no WebSocket was created by the boot sequence/.test(m));

test('a failing boot fetch shows a system error instead of rejecting unhandled', async () => {
  // Fail the very first fetch during boot (loadConfig -> /api/config). The
  // preload hook runs after the harness installs its fetch but before app.js is
  // evaluated, so the boot IIFE sees this failing fetch.
  const env = await boot({
    preload: (win) => {
      win.fetch = async () => {
        throw new Error('config endpoint down');
      };
    },
  });

  const errs = systemErrors(env);
  assert.strictEqual(errs.length, 1, 'exactly one system error is shown on boot failure');
  assert.match(errs[0].textContent, /config endpoint down/, 'the failure reason is surfaced');
  assert.strictEqual(
    realRejections(env).length,
    0,
    'no unhandled rejection escaped -- only the benign no-socket note is present',
  );
});

test('a non-ok response body during boot is surfaced too', async () => {
  const env = await boot({
    preload: (win) => {
      win.fetch = async () => ({
        ok: false,
        status: 503,
        json: async () => ({ detail: 'service unavailable' }),
      });
    },
  });

  const errs = systemErrors(env);
  assert.strictEqual(errs.length, 1, 'the 503 is surfaced, not swallowed');
  assert.match(errs[0].textContent, /service unavailable/);
  assert.strictEqual(
    realRejections(env).length,
    0,
    'the failure is handled, not left as an unhandled rejection',
  );
});

test('a healthy boot shows no system error and rejects nothing (control)', async () => {
  const env = await boot(); // default fixtures: all endpoints ok
  assert.strictEqual(systemErrors(env).length, 0, 'no error on a successful boot');
  assert.strictEqual(env.errors.length, 0, 'a healthy boot creates its socket and logs nothing');
});
