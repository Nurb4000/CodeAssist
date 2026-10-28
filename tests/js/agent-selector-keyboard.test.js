'use strict';
// Regression for §9 a11y: the agent selector declared role="listbox"/option but
// its options were non-focusable divs with no arrow/Enter navigation, so keyboard
// users could only click. The trigger (<button>) now supports ArrowUp/Down/Home/End
// to move the highlighted option, Enter/Space to select, and aria-activedescendant
// points the screen reader at the active option.

const test = require('node:test');
const assert = require('node:assert');
const { boot } = require('./harness.js');

const AGENTS = {
  '/api/agents': [
    { id: 'default', name: 'Full' },
    { id: 'research', name: 'Research' },
  ],
};

const openList = (env) => {
  const trigger = env.document.getElementById('mode-trigger');
  trigger.dispatchEvent(new env.window.MouseEvent('click', { bubbles: true }));
  return trigger;
};

const key = (env, el, keyName) =>
  el.dispatchEvent(new env.window.KeyboardEvent('keydown', { key: keyName, bubbles: true, cancelable: true }));

test('opening the selector highlights the current agent and exposes it to AT', async () => {
  const env = await boot({ fixtures: AGENTS });
  const trigger = openList(env);

  const list = env.document.getElementById('mode-list');
  assert.ok(!list.hidden, 'the listbox opens on trigger click');
  const active = trigger.getAttribute('aria-activedescendant');
  assert.ok(active, 'aria-activedescendant points at the highlighted option');
});

test('arrow keys move the highlighted option around the listbox', async () => {
  const env = await boot({ fixtures: AGENTS });
  const trigger = openList(env);
  const first = trigger.getAttribute('aria-activedescendant');

  key(env, trigger, 'ArrowDown');
  const second = trigger.getAttribute('aria-activedescendant');
  assert.notStrictEqual(second, first, 'ArrowDown moves to the next option');

  key(env, trigger, 'ArrowUp');
  key(env, trigger, 'ArrowUp');
  assert.strictEqual(trigger.getAttribute('aria-activedescendant'), second, 'ArrowUp wraps back to the next option');

  key(env, trigger, 'Home');
  assert.strictEqual(trigger.getAttribute('aria-activedescendant'), first, 'Home jumps to the first option');

  key(env, trigger, 'End');
  assert.strictEqual(trigger.getAttribute('aria-activedescendant'), second, 'End jumps to the last option');
});

test('Enter selects the highlighted agent', async () => {
  const env = await boot({ fixtures: AGENTS });
  const trigger = openList(env);

  // Move to the second option, then activate it.
  key(env, trigger, 'ArrowDown');
  key(env, trigger, 'Enter');

  const research = [...env.document.querySelectorAll('.mode-option')]
    .find((o) => o.dataset.id === 'research');
  assert.ok(research.classList.contains('active'), 'the highlighted agent becomes the active selection');
});

test('Escape closes the listbox', async () => {
  const env = await boot({ fixtures: AGENTS });
  const trigger = openList(env);
  const list = env.document.getElementById('mode-list');
  assert.ok(!list.hidden, 'precondition: list is open');

  key(env, trigger, 'Escape');
  assert.ok(list.hidden, 'Escape collapses the listbox');
});
