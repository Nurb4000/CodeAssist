'use strict';
// Tests for the working indicator and the in-flight UI state.
//
// The reset (send/stop button, input, attachments) used to be copy-pasted into
// five exit paths, so the cancel button and the indicator could disagree with the
// real run state. setBusy() is now the single owner.

const test = require('node:test');
const assert = require('node:assert');
const { boot, startTurn } = require('./harness.js');

const sel = (env, id) => env.document.getElementById(id);

function isBusy(env) {
  return {
    body: env.document.body.classList.contains('is-working'),
    stop: sel(env, 'stop-btn').classList.contains('busy'),
    stopVisible: sel(env, 'stop-btn').style.display === 'flex',
    // No inline display until setBusy() touches it, so test for 'hidden'.
    sendVisible: sel(env, 'send-btn').style.display !== 'none',
    inputDisabled: sel(env, 'user-input').disabled,
  };
}

test('starting a turn shows the working indicator', async () => {
  const env = await boot();
  assert.deepStrictEqual(isBusy(env), {
    body: false, stop: false, stopVisible: false, sendVisible: true, inputDisabled: false,
  });

  startTurn(env);
  assert.deepStrictEqual(isBusy(env), {
    body: true, stop: true, stopVisible: true, sendVisible: false, inputDisabled: true,
  });
});

for (const [label, event] of [
  ['done', { type: 'done' }],
  ['error', { type: 'error', message: 'boom' }],
  ['refusal', { type: 'refusal', code: 'data_inspection_failed', message: 'blocked', suggestions: ['rephrase'] }],
]) {
  test(`the indicator is cleared on ${label}`, async () => {
    const env = await boot();
    startTurn(env);
    assert.ok(isBusy(env).body, 'precondition: busy during the turn');

    env.feed(event);

    assert.deepStrictEqual(isBusy(env), {
      body: false, stop: false, stopVisible: false, sendVisible: true, inputDisabled: false,
    });
  });
}

test('cancelling clears the indicator', async () => {
  const env = await boot();
  startTurn(env);
  assert.ok(isBusy(env).body);

  sel(env, 'stop-btn').click();

  assert.ok(
    env.socket.sent.some((m) => m.type === 'cancel'),
    'the stop button asks the server to cancel'
  );
  // The server confirms by closing the run; the indicator follows that, not the click.
  env.feed({ type: 'done' });
  assert.ok(!isBusy(env).body, 'indicator cleared once the run actually ends');
});

test('the stop button stays available across tool calls in a turn', async () => {
  const env = await boot();
  startTurn(env);
  for (const id of ['t1', 't2', 't3']) {
    env.feed({ type: 'tool_call', name: 'read', arguments: { file_path: 'a.py' }, id });
    env.feed({ type: 'tool_result', id, output: 'ok' });
    assert.ok(isBusy(env).body, `still busy after ${id}`);
    assert.ok(isBusy(env).stopVisible, `cancel stays reachable after ${id}`);
  }
});

test('a second turn can be sent after the previous one finishes', async () => {
  const env = await boot();
  startTurn(env, 'first');
  env.feed({ type: 'done' });

  startTurn(env, 'second');
  assert.ok(isBusy(env).body, 'busy again on the next turn');
  const sent = env.socket.sent.filter((m) => m.type === 'user_message');
  assert.deepStrictEqual(sent.map((m) => m.content), ['first', 'second']);
});

// ── Stop acknowledgement ─────────────────────────────────────────────────────
// Pressing Stop used to look like nothing happened until the server's
// `cancelled` reply came back over the socket, which is a visible delay when
// the turn is mid tool call. The click now shows up immediately, and repeat
// clicks are ignored so the second one cannot interrupt the agent's cleanup.

const cancels = (env) => env.socket.sent.filter((m) => m.type === 'cancel').length;
const isStopping = (env) => sel(env, 'stop-btn').classList.contains('stopping');

test('clicking stop acknowledges the request immediately', async () => {
  const env = await boot();
  startTurn(env);
  assert.ok(!isStopping(env), 'not stopping before the click');

  sel(env, 'stop-btn').click();

  assert.ok(isStopping(env), 'the button shows the stop was taken');
  assert.strictEqual(cancels(env), 1, 'exactly one cancel is sent');
  // Still no `cancelled` from the server yet -- the UI must not wait for it.
  assert.ok(isBusy(env).body, 'the run has not ended yet, by design');
});

test('repeated stop clicks do not re-cancel', async () => {
  const env = await boot();
  startTurn(env);

  sel(env, 'stop-btn').click();
  sel(env, 'stop-btn').click();
  sel(env, 'stop-btn').click();

  assert.strictEqual(
    cancels(env), 1,
    'a second cancel would interrupt the agent writing the partial turn out'
  );
});

test('stop is re-armed for the next run', async () => {
  const env = await boot();
  startTurn(env, 'first');
  sel(env, 'stop-btn').click();
  env.feed({ type: 'cancelled' });
  env.feed({ type: 'done' });
  assert.ok(!isStopping(env), 'the stopping state is cleared when the run ends');

  startTurn(env, 'second');
  assert.ok(!isStopping(env), 'a fresh run re-arms stop');
  sel(env, 'stop-btn').click();
  assert.strictEqual(cancels(env), 2, 'the second run can be stopped too');
});
