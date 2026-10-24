'use strict';
// Regression coverage for §9: createSession / deleteSession had no isStreaming
// guard (unlike switchSession / switchAgent). Calling either mid-run tore down
// the running socket -- the `done` that ends the turn could never arrive, so
// isStreaming stayed true forever (Stop busy, input disabled) and the reconnect
// timer, itself gated on !isStreaming, never fired. The run is wedged until a
// manual reload. Both now bail out while a turn is streaming, mirroring
// switchSession.

const test = require('node:test');
const assert = require('node:assert');
const { boot, startTurn, FakeWebSocket } = require('./harness.js');

test('deleting a session mid-stream keeps the running socket open', async () => {
  const env = await boot();
  startTurn(env);
  assert.strictEqual(env.socket.readyState, FakeWebSocket.OPEN, 'socket open during a turn');

  env.window.confirm = () => true;
  await env.window.deleteSession('session-1');

  assert.strictEqual(
    env.socket.readyState,
    FakeWebSocket.OPEN,
    'a mid-run delete must not tear down the running socket'
  );
});

test('creating a session mid-stream opens no new socket', async () => {
  const env = await boot({
    fixtures: {
      // POST returns a fresh session id; GET still lists the seeded session.
      '/api/sessions': (method) =>
        method === 'POST' ? { id: 'session-new', name: 'new' } : [{ id: 'session-1', name: 'test' }],
    },
  });
  startTurn(env);
  const socketsBefore = env.sockets.length;

  await env.window.createSession();

  assert.strictEqual(
    env.sockets.length,
    socketsBefore,
    'a mid-run create must not open a replacement socket while the turn is live'
  );
  assert.strictEqual(
    env.socket.readyState,
    FakeWebSocket.OPEN,
    'the running socket stays open after a mid-run create'
  );
});
