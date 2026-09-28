'use strict';
// Behavioural tests for the work-block streaming model in static/app.js.
//
// Regression coverage for the reported symptoms: work sections "collapse and
// move around before they should", the container appearing up front with
// everything added at the end, and tool use sometimes not being logged.

const test = require('node:test');
const assert = require('node:assert');
const { boot, startTurn, workBlock, steps, stepOutputs } = require('./harness.js');

/** A turn with one tool call whose result lands normally. */
async function turnWithOneTool(env, { closeStep = false } = {}) {
  env.feed({ type: 'text_delta', content: 'Let me read the file.' });
  env.feed({ type: 'tool_call', name: 'read', arguments: { file_path: 'a.py' }, id: 't1' });
  env.feed({ type: 'tool_result', id: 't1', output: 'print(1)' });
  if (closeStep) {
    // Prose after a tool call closes the step -- the case that used to make it
    // disappear mid-run.
    env.feed({ type: 'text_delta', content: 'Now I will summarise.' });
  }
}

test('the work section stays collapsed while the run is live', async () => {
  const env = await boot();
  startTurn(env);
  await turnWithOneTool(env, { closeStep: true });

  // Completed steps are an accumulator you open on demand, never expanded for
  // you. Nothing force-shows it mid-run any more.
  assert.ok(
    workBlock(env.document).classList.contains('history-hidden'),
    'the work section is collapsed by default while a run is streaming'
  );

  // ...but the content is retained, so expanding shows the whole step.
  const [step] = steps(env.document);
  assert.deepStrictEqual(stepOutputs(step), ['print(1)'], 'the closed step keeps its tool output');
});

test('the live step stays visible outside the collapsed work section', async () => {
  const env = await boot();
  startTurn(env);
  await turnWithOneTool(env, { closeStep: true });

  // A second step opens: it renders in .work-active, which is a SIBLING of the
  // collapsible block, so a collapsed Work section never hides the current step.
  env.feed({ type: 'tool_call', name: 'grep', arguments: { pattern: 'y' }, id: 't2' });
  const live = env.document.querySelectorAll('.work-active .work-step');
  assert.strictEqual(live.length, 1, 'the running step is in the always-visible active zone');
  assert.ok(
    !workBlock(env.document).contains(live[0]),
    'the active zone must not be inside the collapsible block'
  );
});

test('individual steps collapse when the run ends', async () => {
  const env = await boot();
  startTurn(env);
  await turnWithOneTool(env, { closeStep: true });
  assert.ok(steps(env.document)[0].classList.contains('open'), 'precondition');

  env.feed({ type: 'done' });

  assert.ok(
    workBlock(env.document).classList.contains('history-hidden'),
    'the Work section stays collapsed once the run is over'
  );
  assert.ok(!steps(env.document)[0].classList.contains('open'), 'steps collapse on done');
});

test('a late tool result still renders in the step that owns it', async () => {
  const env = await boot();
  startTurn(env);
  env.feed({ type: 'text_delta', content: 'Reading.' });
  env.feed({ type: 'tool_call', name: 'read', arguments: { file_path: 'a.py' }, id: 't1' });
  // The model narrates past the call, closing the step *before* the result
  // arrives. onToolResult used to look only at the active step and dropped it.
  env.feed({ type: 'text_delta', content: 'Meanwhile...' });
  env.feed({ type: 'tool_result', id: 't1', output: 'print(1)' });

  const [step] = steps(env.document);
  assert.deepStrictEqual(
    stepOutputs(step),
    ['print(1)'],
    'the result must not be dropped when its step already closed'
  );
});

test('a result arriving after a new step opened lands in the right step', async () => {
  const env = await boot();
  startTurn(env);
  env.feed({ type: 'text_delta', content: 'One.' });
  env.feed({ type: 'tool_call', name: 'read', arguments: { file_path: 'a.py' }, id: 't1' });
  env.feed({ type: 'text_delta', content: 'Two.' });
  env.feed({ type: 'tool_call', name: 'grep', arguments: { pattern: 'x' }, id: 't2' });
  // Straggler for the *first* step.
  env.feed({ type: 'tool_result', id: 't1', output: 'first-output' });

  const [first, second] = steps(env.document);
  assert.deepStrictEqual(stepOutputs(first), ['first-output'], 'lands in the owning step');
  assert.deepStrictEqual(stepOutputs(second), [], 'not duplicated into the newer step');
});

test('a manual expand of the Work section survives the rest of the run', async () => {
  const env = await boot();
  startTurn(env);
  await turnWithOneTool(env, { closeStep: true });

  const header = workBlock(env.document).querySelector('.work-block-header');

  // User opens it deliberately mid-run; later activity must not re-collapse it.
  header.click();
  assert.ok(!workBlock(env.document).classList.contains('history-hidden'));
  env.feed({ type: 'tool_call', name: 'grep', arguments: { pattern: 'y' }, id: 't2' });
  assert.ok(
    !workBlock(env.document).classList.contains('history-hidden'),
    'a deliberate expand must not be overridden mid-run'
  );

  // And endRun must leave it open.
  env.feed({ type: 'done' });
  assert.ok(
    !workBlock(env.document).classList.contains('history-hidden'),
    'endRun must not re-collapse a section the user opened'
  );
});

test('a new turn restores the collapsed default', async () => {
  const env = await boot();
  startTurn(env, 'first');
  await turnWithOneTool(env);
  workBlock(env.document).querySelector('.work-block-header').click();
  env.feed({ type: 'done' });

  startTurn(env, 'second');
  env.feed({ type: 'tool_call', name: 'read', arguments: { file_path: 'b.py' }, id: 't3' });

  assert.ok(
    workBlock(env.document).classList.contains('history-hidden'),
    'the manual override is per-turn'
  );
});

test('the work count tracks committed steps', async () => {
  const env = await boot();
  startTurn(env);
  await turnWithOneTool(env);
  env.feed({ type: 'text_delta', content: 'again' });
  env.feed({ type: 'tool_call', name: 'grep', arguments: { pattern: 'z' }, id: 't2' });

  const count = workBlock(env.document).querySelector('.work-count').textContent;
  assert.strictEqual(count, '2');
});

test('an error mid-run does not leave steps stuck open', async () => {
  const env = await boot();
  startTurn(env);
  await turnWithOneTool(env);
  env.feed({ type: 'error', message: 'boom' });

  assert.ok(
    workBlock(env.document).classList.contains('history-hidden'),
    'endRun runs on the error path too'
  );
});
