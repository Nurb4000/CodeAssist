'use strict';
// Regression coverage for v2 follow-up delivery: a running turn no longer
// blocks Send, and each frame states explicitly whether it is queued for the
// next turn or steered into the current one at a safe loop boundary.

const test = require('node:test');
const assert = require('node:assert');
const { boot, startTurn } = require('./harness.js');

const sel = (env, id) => env.document.getElementById(id);
const userFrames = (env) => env.socket.sent.filter((m) => m.type === 'user_message');
const progress = (env) => {
  const el = env.document.querySelector('.status-text');
  return el ? el.textContent : null;
};

test('follow-ups default to queue, including sends while busy', async () => {
  const env = await boot();

  startTurn(env, 'first');
  // Precondition: the turn is live, but v2 keeps Send and the input reachable.
  assert.ok(env.document.body.classList.contains('is-working'));
  assert.ok(sel(env, 'send-btn').style.display !== 'none', 'Send stays visible');
  assert.equal(sel(env, 'user-input').disabled, false, 'input stays editable');
  assert.equal(sel(env, 'stop-btn').style.display, 'flex', 'Stop is still visible');

  startTurn(env, 'second');

  assert.deepStrictEqual(
    userFrames(env).map((m) => [m.content, m.delivery]),
    [['first', 'queue'], ['second', 'queue']],
  );
});

test('steer is an explicit choice and does not change later messages', async () => {
  const env = await boot();

  startTurn(env, 'first');
  sel(env, 'delivery-steer').click();
  assert.equal(sel(env, 'delivery-steer').getAttribute('aria-pressed'), 'true');
  assert.equal(sel(env, 'delivery-queue').getAttribute('aria-pressed'), 'false');

  startTurn(env, 'change course');
  sel(env, 'delivery-queue').click();
  startTurn(env, 'third');

  assert.deepStrictEqual(
    userFrames(env).map((m) => [m.content, m.delivery]),
    [['first', 'queue'], ['change course', 'steer'], ['third', 'queue']],
  );
});

test('steer acknowledgements update progress without closing the turn', async () => {
  const env = await boot();
  startTurn(env, 'first');
  env.feed({
    type: 'tool_call',
    name: 'read',
    arguments: { file_path: 'a.py' },
    id: 't1',
  });
  assert.match(progress(env) || '', /Executing read/);

  env.feed({ type: 'steer_queued', content: 'change course' });
  assert.match(progress(env) || '', /steering at the next step/);
  assert.ok(env.document.body.classList.contains('is-working'));

  env.feed({ type: 'steered', content: 'change course' });
  assert.match(progress(env) || '', /Steered/);
  assert.ok(env.document.body.classList.contains('is-working'));

  // A late ack after `done` must not resurrect the spinner or the busy state.
  env.feed({ type: 'done' });
  assert.ok(!env.document.body.classList.contains('is-working'));
  env.feed({ type: 'steered', content: 'late ack' });
  assert.ok(!env.document.body.classList.contains('is-working'));
});
