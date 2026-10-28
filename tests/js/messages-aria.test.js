'use strict';
// Regression for §9 a11y GAP: `#messages` (the chat-flow region) declared no live
// region role, so streamed assistant output, tool steps, errors and system
// notices were invisible to screen readers. It now exposes role="log" with an
// aria-live="polite" region, which is the ARIA pattern for an accumulating,
// non-interactively-updated transcript (aria-atomic=false avoids re-reading the
// whole log on every chunk).

const test = require('node:test');
const assert = require('node:assert');
const { boot } = require('./harness.js');

test('#messages exposes a polite live-log region for screen readers', async () => {
  const env = await boot();
  const messages = env.document.getElementById('messages');
  assert.ok(messages, '#messages exists in the rendered page');
  assert.strictEqual(messages.getAttribute('role'), 'log', 'the chat log is a role=log region');
  assert.strictEqual(
    messages.getAttribute('aria-live'),
    'polite',
    'updates are announced politely (not interruptively)',
  );
  assert.strictEqual(
    messages.getAttribute('aria-atomic'),
    'false',
    'only new content is announced, not the whole log each time',
  );
});
