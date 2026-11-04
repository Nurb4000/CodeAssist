'use strict';
// Regression: a text attachment must carry its browser-reported mime (e.g.
// application/json) all the way to the WS payload, so the backend stores a real
// type instead of defaulting every text file to text/plain. That is what lets
// the parse_json tool classify a JSON attachment. Before the fix the extension
// branch of isTextFile used `in` on a Set (always false), so application/json
// files were rejected outright; this locks in both the classification and the
// mime being forwarded.

const test = require('node:test');
const assert = require('node:assert');
const { boot } = require('./harness.js');

test('attaching a .json forwards application/json in the user_message payload', async () => {
  const env = await boot();
  const { window } = env;

  // isTextFile must accept a json file (the regression that motivated this).
  assert.strictEqual(
    window.isTextFile({ type: 'application/json', name: 'data.json' }),
    true,
    'a .json file is classified as a text attachment',
  );

  const file = new window.File([JSON.stringify({ a: 1 })], 'data.json', {
    type: 'application/json',
  });
  await window.addPendingFile(file);

  window.document.getElementById('user-input').value = 'parse this';
  window.sendMessage();

  const sent = env.socket.sent;
  const msg = sent.find((m) => m && m.type === 'user_message');
  assert.ok(msg, 'a user_message was sent over the socket');
  assert.strictEqual(msg.files.length, 1, 'exactly one file was attached');
  assert.strictEqual(msg.files[0].name, 'data.json');
  assert.strictEqual(msg.files[0].type, 'application/json', 'mime preserved in payload');
});

test('a non-text/* file by extension still forwards its mime', async () => {
  const env = await boot();
  const { window } = env;

  // octet-stream with a .toml extension: isTextFile matches by extension, and
  // the (untrusted) client mime must still be forwarded for the backend to infer.
  const file = new window.File(['k = "v"'], 'config.toml', {
    type: 'application/octet-stream',
  });
  assert.strictEqual(
    window.isTextFile(file),
    true,
    'toml is attached by extension even without a text mime',
  );
  await window.addPendingFile(file);

  window.document.getElementById('user-input').value = 'parse this';
  window.sendMessage();

  const msg = env.socket.sent.find((m) => m && m.type === 'user_message');
  assert.strictEqual(msg.files[0].name, 'config.toml');
  assert.strictEqual(msg.files[0].type, 'application/octet-stream');
});
