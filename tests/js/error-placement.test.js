'use strict';
// Regression coverage for §9: errors/cancellations were injected into the
// user's own message bubble.
//
// ensureMainMessage() reused `lastMainMsgEl`, which appendUserMessage() sets to
// the user's bubble right after sending. So an error/incomplete/cancelled event
// arriving before any reply appended its text INTO the user's message instead of
// its own assistant bubble. The target is now "the latest assistant message, or
// a fresh one" — never the user's own prompt.

const test = require('node:test');
const assert = require('node:assert');
const { boot, startTurn } = require('./harness.js');

const allMessages = (doc) => Array.from(doc.querySelectorAll('#messages > .message'));
const roleOf = (m) => {
  const r = m.querySelector('.message-role');
  return r && r.classList.contains('user') ? 'user' : 'assistant';
};
const contentOf = (m) => (m.querySelector('.message-content') || {}).textContent || '';

// A prose session whose last replayed message is an assistant answer, so
// lastMainMsgEl points at an assistant bubble on load.
const PROSE_SESSION = [
  { id: 'u1', role: 'user', content: 'answer me' },
  { id: 'a1', role: 'assistant', content: 'The answer.', reasoning_content: '' },
];

test('an error before any reply does not render inside the user bubble', async () => {
  const env = await boot();
  startTurn(env, 'what is two plus two');
  env.feed({ type: 'error', message: 'disk full' });

  const messages = allMessages(env.document);
  assert.strictEqual(
    messages.filter((m) => roleOf(m) === 'user').length,
    1,
    'the prompt bubble is still there'
  );
  const assistant = messages.filter((m) => roleOf(m) === 'assistant');
  assert.strictEqual(
    assistant.length,
    1,
    'exactly one fresh assistant bubble holds the error, no duplicate'
  );

  const user = messages.find((m) => roleOf(m) === 'user');
  assert.ok(
    !contentOf(user).includes('disk full'),
    'the error must not leak into the user message'
  );
  assert.ok(
    contentOf(assistant[0]).includes('Error: disk full'),
    'the error renders in its own assistant bubble'
  );
});

test('a cancellation before any reply renders in its own assistant bubble', async () => {
  const env = await boot();
  startTurn(env);
  env.feed({ type: 'cancelled' });

  const assistant = allMessages(env.document).filter((m) => roleOf(m) === 'assistant');
  assert.strictEqual(assistant.length, 1, 'one assistant bubble for the cancellation');
  assert.ok(
    contentOf(assistant[0]).includes('Stopped by user'),
    'the stop notice lands in the assistant bubble, not the prompt'
  );
  const user = allMessages(env.document).find((m) => roleOf(m) === 'user');
  assert.ok(!contentOf(user).includes('Stopped by user'), 'the user bubble stays clean');
});

test('an incomplete warning before any reply renders in its own assistant bubble', async () => {
  const env = await boot();
  startTurn(env);
  env.feed({ type: 'incomplete', message: 'no tool result' });

  const assistant = allMessages(env.document).filter((m) => roleOf(m) === 'assistant');
  assert.strictEqual(assistant.length, 1, 'one assistant bubble for the warning');
  assert.ok(
    contentOf(assistant[0]).includes('no tool result'),
    'the warning renders in the assistant bubble'
  );
});

test('an error appends to an existing assistant message on reload, not a duplicate', async () => {
  // Over-correction guard: when the last main-flow node is already an assistant
  // message (a reloaded prose session), the error must append to that same bubble
  // rather than spawning a second one.
  const env = await boot({
    fixtures: {
      '/api/sessions': [{ id: 'session-1', name: 'one' }],
      '/api/sessions/session-1/messages': PROSE_SESSION,
    },
  });

  env.feed({ type: 'error', message: 'late error' });

  const assistant = allMessages(env.document).filter((m) => roleOf(m) === 'assistant');
  assert.strictEqual(
    assistant.length,
    1,
    'the reloaded assistant message is reused, not duplicated'
  );
  const text = contentOf(assistant[0]);
  assert.ok(text.includes('The answer.'), 'the original answer is preserved');
  assert.ok(text.includes('Error: late error'), 'the error appends to the same bubble');
});
