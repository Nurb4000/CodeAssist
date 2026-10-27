'use strict';
// Regression for §9: `openModal()` attached its Escape listener to the modal
// backdrop. When the modal opened nothing inside it was focused, so an Escape
// keydown targeted the document body / the element that opened the modal and
// never bubbled through the backdrop -- meaning Escape could not dismiss a
// modal at all. The listener now lives on `document` (and is removed on every
// dismissal path, including the export/import Cancel buttons which call
// `bd.remove()` directly).

const test = require('node:test');
const assert = require('node:assert');
const { boot } = require('./harness.js');

const backdrop = (env) => env.document.querySelector('.modal-backdrop');

const pressEscape = (env) =>
  env.document.body.dispatchEvent(
    new env.window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }),
  );

test('Escape dismisses a modal even when nothing inside is focused', async () => {
  const env = await boot();
  env.window.openModal('<p>body</p>', '<button class="modal-close">ok</button>');
  assert.ok(backdrop(env), 'the modal is open');

  pressEscape(env);

  assert.strictEqual(backdrop(env), null, 'Escape closes the modal');
});

test('clicking the backdrop still dismisses (control)', async () => {
  const env = await boot();
  env.window.openModal('<p>body</p>', '<button class="modal-close">ok</button>');

  env.document.querySelector('.modal-backdrop').dispatchEvent(
    new env.window.MouseEvent('click', { bubbles: true }),
  );

  assert.strictEqual(backdrop(env), null, 'clicking the backdrop closes the modal');
});

test('the modal-close button still dismisses (control)', async () => {
  const env = await boot();
  env.window.openModal('<p>body</p>', '<button class="modal-close">ok</button>');

  env.document.querySelector('.modal-close').dispatchEvent(
    new env.window.MouseEvent('click', { bubbles: true }),
  );

  assert.strictEqual(backdrop(env), null, 'the close button dismisses the modal');
});

test('Escape can dismiss a second modal after the first closed (no listener leak)', async () => {
  const env = await boot();

  env.window.openModal('<p>first</p>', '<button class="modal-close">ok</button>');
  pressEscape(env);
  assert.strictEqual(backdrop(env), null, 'first modal closed');

  env.window.openModal('<p>second</p>', '<button class="modal-close">ok</button>');
  pressEscape(env);
  assert.strictEqual(backdrop(env), null, 'second modal also closes on Escape');
});
