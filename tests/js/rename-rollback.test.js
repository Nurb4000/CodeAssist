'use strict';
// Regression coverage for §9: `startRename()` optimistically wrote the new name
// into the sidebar *before* the PATCH, and let a failed PATCH reject unhandled.
// So a rename that the server rejected left the shown name inconsistent with the
// server (and no feedback to the user). Now the optimistic update is wrapped in
// try/except: on failure the name reverts to the original and showError() runs.

const test = require('node:test');
const assert = require('node:assert');
const { boot } = require('./harness.js');

const firstSession = (env) => {
  const item = env.document.querySelector('.session-item');
  return { id: item.dataset.id, nameSpan: item.querySelector('.session-name') };
};

const enterName = (env, input, value) => {
  input.value = value;
  input.dispatchEvent(
    new env.window.KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }),
  );
};

// Fail only the rename PATCH; keep every other fetch (boot already done) benign.
const failingFetch = (env) => {
  env.window.fetch = async (url, opts) => {
    if (opts && opts.method === 'PATCH' && String(url).includes('/api/sessions/')) {
      return { ok: false, status: 500, json: async () => ({ detail: 'rename failed' }) };
    }
    return { ok: true, status: 200, json: async () => ({}) };
  };
};

const settle = () => new Promise((r) => setTimeout(r, 30));

test('a failed rename reverts the name and shows an error', async () => {
  const env = await boot();
  const { id, nameSpan } = firstSession(env);
  const original = nameSpan.textContent;
  failingFetch(env);

  env.window.startRename(id, nameSpan);
  const input = env.document.querySelector('.rename-input');
  assert.ok(input, 'the rename input was created');
  enterName(env, input, 'Renamed Session');
  await settle();

  assert.strictEqual(
    nameSpan.textContent,
    original,
    'the name reverts to the original when the PATCH fails',
  );
  assert.ok(
    env.document.querySelector('#messages .system-error'),
    'a system error is surfaced for the failed rename',
  );
});

test('a successful rename keeps the new name and shows no error (control)', async () => {
  const env = await boot();
  const { id, nameSpan } = firstSession(env);
  const original = nameSpan.textContent;

  // PATCH succeeds.
  env.window.fetch = async (url, opts) => {
    if (opts && opts.method === 'PATCH') {
      return { ok: true, status: 200, json: async () => ({}) };
    }
    return { ok: true, status: 200, json: async () => ({}) };
  };

  env.window.startRename(id, nameSpan);
  const input = env.document.querySelector('.rename-input');
  enterName(env, input, 'Renamed Session');
  await settle();

  assert.strictEqual(nameSpan.textContent, 'Renamed Session', 'the new name sticks');
  assert.strictEqual(
    env.document.querySelectorAll('#messages .system-error').length,
    0,
    'no error on a successful rename',
  );
});
