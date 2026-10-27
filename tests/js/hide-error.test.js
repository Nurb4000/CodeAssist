'use strict';
// Regression coverage for §9: hideError() only removed the *last* .system-error,
// so any earlier error (a failed create/delete, an older reconnect banner)
// outlived the call and stayed on screen forever. hideError() is the recovery
// path, so it now clears every system error.

const test = require('node:test');
const assert = require('node:assert');
const { boot } = require('./harness.js');

const systemErrors = (env) =>
  Array.from(env.document.querySelectorAll('#messages .system-error'));

test('hideError clears every system error, not just the last', async () => {
  const env = await boot();

  env.window.showError('first failure');
  env.window.showError('second failure');
  assert.strictEqual(systemErrors(env).length, 2, 'two errors are on screen');

  env.window.hideError();

  assert.strictEqual(systemErrors(env).length, 0, 'all system errors are cleared');
});

test('hideError is a no-op when there are no errors', async () => {
  const env = await boot();
  assert.strictEqual(systemErrors(env).length, 0, 'clean boot shows no error');
  env.window.hideError(); // must not throw
  assert.strictEqual(systemErrors(env).length, 0, 'still nothing after a no-op hide');
});
