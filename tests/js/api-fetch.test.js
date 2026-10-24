'use strict';
// Regression coverage for the shared fetch helper (§11 duplication).
//
// app.js and admin.js each defined their own api(). The admin one was strong
// (safe JSON parse + throws on a non-ok response); the app.js one was weak:
// it returned res.json() directly, so a non-JSON error body threw an unhandled
// rejection and any non-ok response (500 page, plain-text failure) was silently
// treated as success. This aligns app.js with admin.js and locks it in.

const test = require('node:test');
const assert = require('node:assert');
const { boot } = require('./harness.js');

test('api() throws on a non-ok response instead of swallowing it', async () => {
  const env = await boot();
  env.window.fetch = async () => ({
    ok: false,
    status: 500,
    json: async () => ({ detail: 'backend down' }),
  });

  await assert.rejects(
    () => env.window.api('GET', '/api/whatever'),
    /backend down/,
    'the server-provided error detail must surface, not be discarded'
  );
});

test('api() surfaces a generic HTTP status when the body has no detail', async () => {
  const env = await boot();
  env.window.fetch = async () => ({
    ok: false,
    status: 401,
    json: async () => ({}),
  });

  await assert.rejects(
    () => env.window.api('GET', '/api/whatever'),
    /HTTP 401/,
    'falls back to the status code when no detail/message is present'
  );
});

test('api() returns {} on an ok response with an unparseable body', async () => {
  const env = await boot();
  // ok but body is not JSON (e.g. an HTML error page served with 200): the old
  // helper threw an unhandled rejection here; the strong one returns {}.
  env.window.fetch = async () => ({
    ok: true,
    status: 200,
    json: async () => { throw new Error('Unexpected token < in JSON'); },
  });

  const data = await env.window.api('GET', '/api/whatever');
  // Object.keys() returns a fresh array in this realm, avoiding a cross-realm
  // deepStrictEqual quirk (the api() {} comes from the jsdom VM realm).
  assert.strictEqual(
    Object.keys(data).length,
    0,
    'a non-JSON ok body degrades to an empty object instead of throwing'
  );
});
