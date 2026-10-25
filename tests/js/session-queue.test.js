'use strict';
// Regression coverage for §9: a message queued during a disconnect was retried
// against the live `ws`, which connectWS()/switchSession() swap out. So if the
// user switched sessions while the queue was pending, the message was delivered
// on the new session's socket -- wrong turn. The retry now captures the session
// it queued against and abandons the queue if that changes.

const test = require('node:test');
const assert = require('node:assert');
const { boot, FakeWebSocket } = require('./harness.js');

const TWO_SESSIONS = {
  '/api/sessions': [{ id: 'session-1', name: 'one' }, { id: 'session-2', name: 'two' }],
  '/api/sessions/session-1/messages': [],
  '/api/sessions/session-2/messages': [],
};

const setInput = (env, text) => {
  env.window.document.getElementById('user-input').value = text;
};

const userMessages = (env) =>
  env.sockets.flatMap((s) => (s.sent || []).filter((m) => m.type === 'user_message'));

test('a queued message is delivered once its own session reconnects', async () => {
  const env = await boot({ fixtures: TWO_SESSIONS });
  // Force the "disconnected" path so sendMessage() queues instead of sending.
  env.socket.close();
  assert.notStrictEqual(env.socket.readyState, FakeWebSocket.OPEN);

  setInput(env, 'hello');
  env.window.sendMessage();
  await new Promise((r) => setTimeout(r, 700));

  const sent = userMessages(env);
  assert.strictEqual(sent.length, 1, 'the retry delivers the queued message');
  assert.strictEqual(sent[0].content, 'hello', 'the queued content is preserved');
});

test('a queued message is abandoned if the session switches while pending', async () => {
  const env = await boot({ fixtures: TWO_SESSIONS });
  env.socket.close();

  setInput(env, 'hello');
  env.window.sendMessage(); // queues against session-1, reconnects on a fresh socket
  await env.window.switchSession('session-2'); // swap the live socket mid-queue

  await new Promise((r) => setTimeout(r, 700));

  const sent = userMessages(env);
  assert.strictEqual(
    sent.length,
    0,
    'the queued message must not be delivered to the session the user switched to'
  );
});
