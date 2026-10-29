'use strict';
// Regression for a §10 GAP: the admin edit modal could only be closed with the
// Cancel button or by clicking the backdrop -- pressing Escape did nothing, so
// keyboard users were trapped inside it. openEditModal() now installs an
// Escape keydown handler (removed in closeEditModal(), held at module scope so
// the removal targets the exact listener added) and closeEditModal() tears it
// down, so Esc closes and repeated opens do not stack listeners.

const test = require('node:test');
const assert = require('node:assert');
const { bootAdmin } = require('./harness.js');

const backdrop = (env) => env.document.querySelector('.modal-backdrop');

const openMinimalModal = (env) => {
  env.window.openEditModal(
    'Test',
    [{ key: 'name', label: 'Name', type: 'text', value: '', required: true }],
    async () => {},
  );
};

const escape = (env) =>
  env.document.dispatchEvent(
    new env.window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }),
  );

test('an open edit modal closes on Escape', async () => {
  const env = await bootAdmin();
  openMinimalModal(env);
  assert.ok(backdrop(env), 'precondition: the modal is open');

  escape(env);

  assert.strictEqual(backdrop(env), null, 'Escape closes the modal');
});

test('a non-Escape key does not close the modal', async () => {
  const env = await bootAdmin();
  openMinimalModal(env);

  env.document.dispatchEvent(
    new env.window.KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }),
  );

  assert.ok(backdrop(env), 'the modal stays open on a non-Escape key');
});

test('repeated open/close cycles do not stack keydown listeners', async () => {
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
      openMinimalModal(env);
      assert.ok(backdrop(env), `modal open on cycle ${i + 1}`);
      escape(env);
      assert.strictEqual(backdrop(env), null, `modal closed on cycle ${i + 1}`);
    }
    // One listener added per open, one removed per close -- balanced, no leak.
    assert.strictEqual(adds, 3, 'a keydown listener is added for each open');
    assert.strictEqual(removes, 3, 'that listener is removed on each close');
  } finally {
    win.addEventListener = realAdd;
    win.removeEventListener = realRemove;
  }
});
