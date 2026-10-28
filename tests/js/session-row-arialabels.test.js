'use strict';
// Regression for §9 a11y GAP: the session-row icon buttons (pin / rename /
// export / delete) exposed only `title` tooltips, which assistive tech does not
// reliably announce. Each button now also carries an `aria-label`, so a screen
// reader user hears "Pin session", "Rename session", etc. instead of a bare,
// unlabeled icon button.

const test = require('node:test');
const assert = require('node:assert');
const { boot } = require('./harness.js');

test('every session-row icon button has an accessible name', async () => {
  const env = await boot();

  for (const [selector, expected] of [
    ['.pin-btn', 'Pin session'],
    ['.rename-btn', 'Rename session'],
    ['.export-btn', 'Export session'],
    ['.delete-btn', 'Delete session'],
  ]) {
    const btn = env.document.querySelector(`.session-item ${selector}`);
    assert.ok(btn, `${selector} is rendered for the seeded session`);
    const label = btn.getAttribute('aria-label');
    assert.strictEqual(label, expected, `${selector} exposes an aria-label`);
  }
});
