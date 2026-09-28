'use strict';
// Behavioural tests for thinking/reasoning visibility during a live run.
//
// Regression coverage for: "the thinking indicators were only blinking
// momentarily on the screen, then vanish; no thinking container was created."
// Root causes fixed:
//   1. Leading reasoning streamed into a detached div -> invisible until the
//      first tool call. startPendingUnit() now attaches the unit immediately.
//   2. closeActiveStep() archived a step's thinking into the collapsed
//      past-thinking container the instant the model narrated past a tool call,
//      so thinking blinked out mid-run. It now only archives at endRun().

const test = require('node:test');
const assert = require('node:assert');
const { boot, startTurn, workBlock, steps } = require('./harness.js');

const q = (doc, sel) => doc.querySelector(sel);
const all = (doc, sel) => Array.from(doc.querySelectorAll(sel));
const thinkingText = (doc) =>
  all(doc, '.thinking-content .thinking-content, .thinking-history .thinking-content, .thinking-block .thinking-content')
    .map((c) => c.textContent)
    .join(' ');

test('leading reasoning is visible before the first tool call', async () => {
  const env = await boot();
  startTurn(env);
  env.feed({ type: 'reasoning', content: 'Let me think step by step about this.' });
  env.feed({ type: 'reasoning', content: ' more reasoning' });

  assert.ok(
    all(env.document, '.thinking-block').length >= 1,
    'a thinking block must exist while reasoning streams'
  );
  const contents = all(env.document, '.thinking-content').map((c) => c.textContent);
  assert.ok(
    contents.some((t) => t.includes('Let me think step by step')),
    'streamed reasoning must actually be rendered'
  );
});

test('mid-run step close keeps its thinking visible (not archived)', async () => {
  const env = await boot();
  startTurn(env);
  env.feed({ type: 'reasoning', content: 'Plan the change.' });
  env.feed({ type: 'tool_call', name: 'edit', arguments: { file_path: 'a.py', replacement: 'x' }, id: 't1' });
  env.feed({ type: 'tool_result', id: 't1', output: 'ok' });
  // Model narrates past the tool call -> step closes, but the run is still on.
  env.feed({ type: 'text_delta', content: 'Now I will verify.' });

  assert.strictEqual(all(env.document, '.work-step').length, 1, 'the step exists');
  assert.strictEqual(
    all(env.document, '.work-step .thinking-block').length, 1,
    'the closed step must keep its thinking block mid-run'
  );
  assert.strictEqual(
    all(env.document, '.past-thinking-entry').length, 0,
    'nothing may be archived into past thinking mid-run'
  );
  assert.strictEqual(q(env.document, '.thinking-history'), null, 'no past-thinking container mid-run');
  assert.ok(
    all(env.document, '.work-step .thinking-content').some((c) => c.textContent.includes('Plan the change.')),
    'the reasoning text stays in the step'
  );
});

test('a completed mid-run turn keeps its thinking during the run', async () => {
  const env = await boot();
  startTurn(env);
  env.feed({ type: 'reasoning', content: 'First step reasoning.' });
  env.feed({ type: 'tool_call', name: 'grep', arguments: { pattern: 'foo' }, id: 't1' });
  env.feed({ type: 'tool_result', id: 't1', output: 'matches' });
  env.feed({ type: 'text_delta', content: 'Now step two.' });
  env.feed({ type: 'reasoning', content: 'Second step reasoning.' });
  env.feed({ type: 'tool_call', name: 'read', arguments: { file_path: 'b.py' }, id: 't2' });
  env.feed({ type: 'tool_result', id: 't2', output: 'def f(): pass' });

  const steps = all(env.document, '.work-step');
  assert.strictEqual(steps.length, 2, 'two work steps committed');
  assert.strictEqual(
    all(env.document, '.work-step .thinking-block').length, 2,
    'both steps keep their thinking while the run is live'
  );
});

test('the summary\'s own reasoning is filed when the run ends', async () => {
  // The last LLM turn is prose + reasoning, never a tool call, so its reasoning
  // streams into the pending unit. That used to be the one piece of thinking that
  // stayed on screen after the run finished.
  const env = await boot();
  startTurn(env);
  env.feed({ type: 'reasoning', content: 'Step reasoning.' });
  env.feed({ type: 'tool_call', name: 'read', arguments: { file_path: 'a.py' }, id: 't1' });
  env.feed({ type: 'tool_result', id: 't1', output: 'x' });
  env.feed({ type: 'reasoning', content: 'Summary reasoning.' });
  env.feed({ type: 'text_delta', content: 'All done.' });
  env.feed({ type: 'done' });

  assert.strictEqual(
    all(env.document, '#messages > .message > .thinking-block').length, 0,
    'no inline thinking block survives on the summary message'
  );
  assert.strictEqual(all(env.document, '.past-thinking-entry').length, 2, 'both reasonings are filed');
  assert.ok(
    all(env.document, '.past-thinking-entry .thinking-content')
      .some((c) => c.textContent.includes('Summary reasoning.')),
    'the summary reasoning reaches the past-thinking container'
  );
  // The answer itself must survive in the main flow.
  assert.ok(
    all(env.document, '#messages > .message .message-content')
      .some((c) => c.textContent.includes('All done.')),
    'the summary prose is still in the main flow'
  );
});

test('a prose-only turn does not leave an empty thinking entry', async () => {
  const env = await boot();
  startTurn(env);
  env.feed({ type: 'text_delta', content: 'Just an answer, no reasoning.' });
  env.feed({ type: 'done' });

  assert.strictEqual(
    all(env.document, '.past-thinking-entry').length, 0,
    'an empty thinking block must not be filed'
  );
  assert.ok(
    all(env.document, '#messages > .message .message-content')
      .some((c) => c.textContent.includes('Just an answer')),
    'the message is still rendered'
  );
});

test('end of run archives all thinking into the past-thinking container', async () => {
  const env = await boot();
  startTurn(env);
  env.feed({ type: 'reasoning', content: 'Plan the change.' });
  env.feed({ type: 'tool_call', name: 'edit', arguments: { file_path: 'a.py', replacement: 'x' }, id: 't1' });
  env.feed({ type: 'tool_result', id: 't1', output: 'ok' });
  env.feed({ type: 'text_delta', content: 'Done.' });
  env.feed({ type: 'done' });

  const container = q(env.document, '.thinking-history');
  assert.ok(container, 'past-thinking container is created once the run ends');
  assert.strictEqual(
    all(env.document, '.past-thinking-entry').length, 1,
    'the completed step reasoning is archived'
  );
  assert.strictEqual(
    all(env.document, '.work-step .thinking-block').length, 0,
    'no thinking remains inline in the work steps after the run'
  );
  assert.ok(
    all(env.document, '.past-thinking-entry .thinking-content')
      .some((c) => c.textContent.includes('Plan the change.')),
    'archived reasoning keeps its text'
  );
});