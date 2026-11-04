'use strict';
// Regression coverage for the file-attachment classifier (re-scan M7 / json attach bug).
//
// isTextFile() decided text-ness as:
//   file.type.startsWith('text/') || ext.toLowerCase() in TEXT_FILE_EXTS
// where TEXT_FILE_EXTS is a Set. The `in` operator checks for *own properties*,
// not Set membership, so the extension branch was always false. Only files the
// browser labels text/* attached; application/json (and any non-text/* mime by
// extension) was rejected as "unsupported". The fix uses Set.has().

const test = require('node:test');
const assert = require('node:assert');
const { boot } = require('./harness.js');

test('isTextFile accepts a .json file (application/json)', async () => {
  const env = await boot();
  assert.strictEqual(typeof env.window.isTextFile, 'function', 'isTextFile is exposed');
  const ok = env.window.isTextFile({ type: 'application/json', name: 'data.json' });
  assert.strictEqual(ok, true, 'json must be treated as a text file for attachment');
});

test('isTextFile accepts an uppercase extension with no mime', async () => {
  const env = await boot();
  assert.strictEqual(
    env.window.isTextFile({ type: '', name: 'config.JSON' }),
    true,
    'extension match must be case-insensitive even when the browser reports no mime',
  );
});

test('isTextFile accepts a text/* file', async () => {
  const env = await boot();
  assert.strictEqual(
    env.window.isTextFile({ type: 'text/plain', name: 'notes.txt' }),
    true,
    'text/* files attach',
  );
});

test('isTextFile rejects a real image', async () => {
  const env = await boot();
  assert.strictEqual(
    env.window.isTextFile({ type: 'image/png', name: 'a.png' }),
    false,
    'images are handled by the image path, not isTextFile',
  );
});
