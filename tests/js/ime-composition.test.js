'use strict';
// Regression coverage for §9: the input's Enter keydown handler sent on every
// Enter, even while an IME (input method editor) was composing -- i.e. when a
// CJK / Korean / Russian user pressed Enter to *confirm candidate text*. The
// partial preedit was sent as a message and composition was broken. The handler
// now bails while `e.isComposing` is true.

const test = require('node:test');
const assert = require('node:assert');
const { boot } = require('./harness.js');

const userMessages = (env) =>
  env.sockets.flatMap((s) => (s.sent || []).filter((m) => m.type === 'user_message'));

const setInput = (env, text) => {
  env.document.getElementById('user-input').value = text;
};

const pressEnter = (env, isComposing) => {
  env.document.getElementById('user-input').dispatchEvent(
    new env.window.KeyboardEvent('keydown', {
      key: 'Enter',
      bubbles: true,
      cancelable: true,
      isComposing,
    }),
  );
};

test('Enter sends a normal message when not composing', async () => {
  const env = await boot();
  setInput(env, 'hello');
  pressEnter(env, false);
  assert.strictEqual(userMessages(env).length, 1, 'Enter sends while idle');
});

test('Enter does not send during IME composition', async () => {
  const env = await boot();
  setInput(env, 'partial');
  pressEnter(env, true);
  assert.strictEqual(userMessages(env).length, 0, 'Enter confirms candidate text instead of sending');
});
