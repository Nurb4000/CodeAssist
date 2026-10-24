'use strict';
// Regression coverage for §9: after the user stops a turn, the server sends
// `cancelled` (which shows "Stopped by user") followed by `done`. The `done`
// handler only suppressed the "✓ Complete" badge when the turn had been
// refused by a provider filter (runWasRefused), so a stopped turn showed BOTH
// badges. A stop is not a success -- the "Complete" badge must not appear.

const test = require('node:test');
const assert = require('node:assert');
const { boot, startTurn } = require('./harness.js');

const messagesHtml = (doc) => doc.querySelector('#messages').innerHTML;

test('a stopped turn shows "Stopped by user" but not "✓ Complete"', async () => {
  const env = await boot();
  startTurn(env, 'run the thing');
  env.feed({ type: 'cancelled' });
  env.feed({ type: 'done' });

  const html = messagesHtml(env.document);
  assert.ok(html.includes('Stopped by user'), 'the stop indicator is shown');
  assert.ok(!html.toLowerCase().includes('complete'),
    'a cancelled turn must not also show a "✓ Complete" badge');
});

test('a normal completion still shows the "✓ Complete" badge', async () => {
  const env = await boot();
  startTurn(env, 'run the thing');
  env.feed({ type: 'done' });

  assert.ok(messagesHtml(env.document).toLowerCase().includes('complete'),
    'a clean done should still mark the turn complete');
});
