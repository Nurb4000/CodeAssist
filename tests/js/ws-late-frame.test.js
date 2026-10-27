'use strict';
// Regression for §9: on a session switch connectWS() swaps `ws` for a fresh
// socket, but frames still in flight on the previous session's socket could
// still dispatch into the new one. Each socket's onmessage now guards on
// identity (`thisWs !== ws`) so a late frame from a swapped-out socket is
// dropped instead of dispatched.

const test = require('node:test');
const assert = require('node:assert');
const { boot } = require('./harness.js');

const TWO_SESSIONS = {
  '/api/sessions': [
    { id: 'session-1', name: 'one' },
    { id: 'session-2', name: 'two' },
  ],
  '/api/sessions/session-1/messages': [],
  '/api/sessions/session-2/messages': [],
  '/api/todos': [],
};

const renderedText = (env) =>
  Array.from(env.document.querySelectorAll('#messages .message-content'))
    .map((c) => c.textContent)
    .join(' ');

test('a late frame on the old socket after a switch is ignored', async () => {
  const env = await boot({ fixtures: TWO_SESSIONS });
  const oldSocket = env.sockets[0]; // session-1's socket, current at boot

  await env.window.switchSession('session-2');
  await new Promise((r) => setTimeout(r, 20));

  // A frame still in flight on the previous session's socket. With the guard it
  // is dropped; without it, the stale handler dispatches 'STALE' into session-2.
  oldSocket.receive({ type: 'text_delta', content: 'STALE' });
  await new Promise((r) => setTimeout(r, 20));

  assert.ok(
    !renderedText(env).includes('STALE'),
    'a late frame from the old session is not rendered',
  );
});

test('the current socket still dispatches live frames after a switch (control)', async () => {
  const env = await boot({ fixtures: TWO_SESSIONS });

  await env.window.switchSession('session-2');
  await new Promise((r) => setTimeout(r, 20));

  // env.feed() is bound to the boot-time socket, which is stale after the switch;
  // deliver to the current one directly to prove live frames still dispatch.
  const current = env.sockets[env.sockets.length - 1];
  current.receive({ type: 'text_delta', content: 'Live from session-2' });
  await new Promise((r) => setTimeout(r, 20));

  assert.ok(
    renderedText(env).includes('Live from session-2'),
    'the current socket still dispatches',
  );
});
