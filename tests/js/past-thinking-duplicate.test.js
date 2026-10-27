'use strict';
// Regression coverage for §9: `beginTurnSections()` (called at the start of every
// turn) nulled the past-thinking handles but left the old container in the DOM.
// Starting a second turn in the same session therefore created a *second*
// ".thinking-history" container alongside the first -- an extra "Past thinking
// (0)" section sitting next to the real one holding last turn's reasoning.

const test = require('node:test');
const assert = require('node:assert');
const { boot, startTurn } = require('./harness.js');

const all = (doc, sel) => Array.from(doc.querySelectorAll(sel));

// A tool-using turn whose reasoning is archived into the past-thinking container
// when the run ends (mirrors the existing thinking.test.js scenarios).
async function reasonedToolTurn(env, prompt, thought) {
  startTurn(env, prompt);
  env.feed({ type: 'reasoning', content: thought });
  env.feed({ type: 'tool_call', name: 'read', arguments: { file_path: 'a.py' }, id: 't1' });
  env.feed({ type: 'tool_result', id: 't1', output: 'x' });
  env.feed({ type: 'done' });
}

test('a second turn in the same session does not duplicate the past-thinking container', async () => {
  const env = await boot();

  await reasonedToolTurn(env, 'first', 'First turn thought.');
  assert.strictEqual(
    all(env.document, '.thinking-history').length,
    1,
    'exactly one container after the first turn',
  );

  await reasonedToolTurn(env, 'second', 'Second turn thought.');

  assert.strictEqual(
    all(env.document, '.thinking-history').length,
    1,
    'the second turn reuses the container instead of leaving a duplicate',
  );
  // Both turns' reasoning must still be reachable in the single container.
  const text = all(env.document, '.past-thinking-entry .thinking-content')
    .map((c) => c.textContent)
    .join(' ');
  assert.ok(text.includes('First turn thought.'), 'first turn reasoning is preserved');
  assert.ok(text.includes('Second turn thought.'), 'second turn reasoning is preserved');
});
