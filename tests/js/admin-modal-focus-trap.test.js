'use strict';
// Regression for a §10 GAP (a11y): the admin edit modal closed on Escape but did
// not trap focus, so a keyboard user tabbing past the last control could reach
// the nav and section toggles behind the backdrop. openEditModal() now installs a
// Tab handler that wraps focus between the first and last focusable control while
// the modal is open; closeEditModal() removes it (held at module scope, like the
// Escape handler, so repeated opens do not stack listeners).

const test = require('node:test');
const assert = require('node:assert');
const { bootAdmin } = require('./harness.js');

const focusables = (env) =>
  Array.from(
    env.document.querySelectorAll(
      '.modal-card input:not([type="hidden"]), .modal-card textarea, .modal-card select, .modal-card button',
    ),
  ).filter((el) => !el.disabled);

const openProbeModal = (env) =>
  env.window.openEditModal(
    'Probe',
    [
      { key: 'a', label: 'A', type: 'text', value: '', required: true },
      { key: 'b', label: 'B', type: 'text', value: '', required: false },
      { key: 'c', label: 'C', type: 'text', value: '', required: false },
    ],
    async () => {},
  );

const tab = (env, shift = false) => {
  const ev = new env.window.KeyboardEvent('keydown', { key: 'Tab', shiftKey: shift, bubbles: true, cancelable: true });
  env.document.dispatchEvent(ev);
  return ev;
};

test('opening the modal focuses the first control', async () => {
  const env = await bootAdmin();
  openProbeModal(env);
  assert.strictEqual(env.document.activeElement, focusables(env)[0], 'focus lands on the first field');
});

test('Tab at the last control wraps focus to the first', async () => {
  const env = await bootAdmin();
  openProbeModal(env);
  const list = focusables(env);
  list[list.length - 1].focus();

  const ev = tab(env, false);

  assert.ok(ev.defaultPrevented, 'the trailing Tab is intercepted');
  assert.strictEqual(env.document.activeElement, list[0], 'focus wraps to the first control');
});

test('Shift+Tab at the first control wraps focus to the last', async () => {
  const env = await bootAdmin();
  openProbeModal(env);
  const list = focusables(env);
  list[0].focus();

  const ev = tab(env, true);

  assert.ok(ev.defaultPrevented, 'the leading Shift+Tab is intercepted');
  assert.strictEqual(env.document.activeElement, list[list.length - 1], 'focus wraps to the last control');
});

test('Tab in the middle of the list is not intercepted', async () => {
  const env = await bootAdmin();
  openProbeModal(env);
  const list = focusables(env);

  list[1].focus();
  const ev = tab(env, false);

  assert.ok(!ev.defaultPrevented, 'a mid-list Tab passes through untouched');
  assert.strictEqual(env.document.activeElement, list[1], 'focus stays where it was');
});

test('open/close cycles do not stack keydown listeners', async () => {
  const env = await bootAdmin();
  const win = env.window;

  let adds = 0;
  let removes = 0;
  const realAdd = win.addEventListener.bind(win);
  const realRemove = win.removeEventListener.bind(win);
  win.addEventListener = (type, ...rest) => {
    if (type === 'keydown') adds += 1;
    return realAdd(type, ...rest);
  };
  win.removeEventListener = (type, ...rest) => {
    if (type === 'keydown') removes += 1;
    return realRemove(type, ...rest);
  };

  try {
    for (let i = 0; i < 3; i++) {
      openProbeModal(env);
      assert.ok(focusables(env).length, `modal open on cycle ${i + 1}`);
      env.window.closeEditModal();
      assert.strictEqual(focusables(env).length, 0, `modal closed on cycle ${i + 1}`);
    }
    // Each open adds one keydown listener (Escape) and each close removes it;
    // the trap adds the same way. Balanced means no leak.
    assert.strictEqual(adds, removes, 'keydown listeners are balanced after three open/close cycles');
  } finally {
    win.addEventListener = realAdd;
    win.removeEventListener = realRemove;
  }
});
