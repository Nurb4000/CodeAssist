'use strict';
// The manual "Compact context" action is a non-destructive checkpoint: the UI
// sends a `compact` frame, disables the button while the server summarizes, and
// re-arms it when the result (success or error) comes back.

const test = require('node:test');
const assert = require('node:assert');
const { boot } = require('./harness.js');

test('compact button exists and sends a compact frame', async () => {
  const env = await boot();
  const btn = env.document.getElementById('compact-btn');
  assert.ok(btn, 'the footer has a Compact context button');

  btn.click();

  assert.ok(
    env.socket.sent.some((f) => f.type === 'compact'),
    'clicking sends a compact frame over the live socket',
  );
  assert.equal(btn.disabled, true, 'button is disabled while the checkpoint is being saved');
  assert.deepEqual(env.errors, []);
});

test('checkpoint_saved re-arms the button and confirms what was covered', async () => {
  const env = await boot();
  const btn = env.document.getElementById('compact-btn');
  btn.click();

  env.feed({
    type: 'checkpoint_saved',
    message: 'Context checkpoint saved.',
    covered: 6,
    summary_chars: 120,
  });

  assert.equal(btn.disabled, false, 'button is re-armed after a successful save');
  const statuses = Array.from(env.document.querySelectorAll('.system-status'))
    .map((n) => n.textContent).join('\n');
  assert.match(statuses, /checkpoint saved/i);
  assert.match(statuses, /6 messages covered/);
  assert.deepEqual(env.errors, []);
});

test('an error re-arms the button so the user can retry', async () => {
  const env = await boot();
  const btn = env.document.getElementById('compact-btn');
  btn.click();

  env.feed({ type: 'error', message: 'Not enough history to compact yet.' });

  assert.equal(btn.disabled, false, 'button is re-armed on error');
  assert.deepEqual(env.errors, []);
});

test('compacting keeps the button disabled until the result arrives', async () => {
  const env = await boot();
  const btn = env.document.getElementById('compact-btn');
  btn.click();

  env.feed({ type: 'compacting', message: 'Saving a context checkpoint...', reason: 'manual' });
  assert.equal(btn.disabled, true, 'the in-progress frame does not re-arm the button');

  env.feed({ type: 'checkpoint_saved', message: 'Context checkpoint saved.', covered: 2 });
  assert.equal(btn.disabled, false);
  assert.deepEqual(env.errors, []);
});
