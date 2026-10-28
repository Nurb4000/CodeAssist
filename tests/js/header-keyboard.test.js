'use strict';
// Regression for §9 a11y: the collapsible section headers (Work block, work step,
// tool call, past-thinking) were <div>s with only a click listener, so keyboard
// and screen-reader users could not toggle them -- a div is neither focusable nor
// announced as activatable, and Enter/Space did nothing. makeHeaderKeyboardable
// now gives each role="button" + tabindex="0" + an Enter/Space keydown that calls
// the same toggle.

const test = require('node:test');
const assert = require('node:assert');
const { boot, startTurn } = require('./harness.js');

// Run a tool-using turn so a work block, a work step and a tool call exist.
async function reasonedTurn(env) {
  startTurn(env);
  env.feed({ type: 'reasoning', content: 'Let me think.' });
  env.feed({ type: 'tool_call', name: 'read', arguments: { file_path: 'a.py' }, id: 't1' });
  env.feed({ type: 'tool_result', id: 't1', output: 'print(1)' });
  env.feed({ type: 'text_delta', content: 'Done.' });
  env.feed({ type: 'done' });
}

const keydown = (env, el, key) =>
  el.dispatchEvent(new env.window.KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }));

test('the Work section header is a keyboard-activatable button', async () => {
  const env = await boot();
  await reasonedTurn(env);

  const header = env.document.querySelector('.work-block-header');
  const block = header.closest('.work-block');
  assert.ok(header, 'the work block header exists');
  assert.strictEqual(header.getAttribute('role'), 'button', 'announced as a button to AT');
  assert.strictEqual(header.getAttribute('tabindex'), '0', 'keyboard focusable');

  // Collapsed by default; Enter expands it via the keydown handler.
  assert.ok(block.classList.contains('history-hidden'), 'collapsed initially');
  keydown(env, header, 'Enter');
  assert.ok(!block.classList.contains('history-hidden'), 'Enter toggles the section open');

  keydown(env, header, ' ');
  assert.ok(block.classList.contains('history-hidden'), 'Space toggles it back closed');
});

test('a work step header exposes button semantics', async () => {
  const env = await boot();
  await reasonedTurn(env);

  const header = env.document.querySelector('.work-step-header');
  assert.ok(header, 'the step header exists');
  assert.strictEqual(header.getAttribute('role'), 'button', 'step header is a button');
  assert.strictEqual(header.getAttribute('tabindex'), '0', 'step header is focusable');
});

test('a tool call header exposes button semantics', async () => {
  const env = await boot();
  await reasonedTurn(env);

  const header = env.document.querySelector('.tool-call-header');
  assert.ok(header, 'the tool call header exists');
  assert.strictEqual(header.getAttribute('role'), 'button', 'tool call header is a button');
  assert.strictEqual(header.getAttribute('tabindex'), '0', 'tool call header is focusable');
});
