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

// ── Deleting a session ────────────────────────────────────────────────────────
// deleteSession() replaced #messages wholesale but left the cached Work-block
// handles pointing at the detached node, so every step after a delete was built
// inside an orphan and never appeared. Same failure shape as the past-thinking
// container, and it is what made the two sections behave inconsistently.

test('a run after deleting and recreating a session renders its work block', async () => {
  const env = await boot({
    fixtures: {
      '/api/sessions': (method) =>
        method === 'POST' ? { id: 'session-3' } : [{ id: 'session-1', name: 'one' }],
    },
  });
  startTurn(env);
  env.feed({ type: 'tool_call', name: 'read', arguments: { file_path: 'a.py' }, id: 't1' });
  env.feed({ type: 'tool_result', id: 't1', output: 'before delete' });
  env.feed({ type: 'done' });
  assert.ok(workBlock(env.document), 'precondition: a work block exists');

  // Deleting the active session confirms, then wipes the flow and drops the
  // socket (there is no session left to connect to).
  env.window.confirm = () => true;
  await env.window.deleteSession('session-1');
  await new Promise((r) => setTimeout(r, 20));
  assert.strictEqual(workBlock(env.document), null, 'the flow really was wiped');

  // The realistic recovery path: start a new session, which reconnects.
  await env.window.createSession();
  await new Promise((r) => setTimeout(r, 20));
  const socket = env.sockets[env.sockets.length - 1];

  env.window.document.getElementById('user-input').value = 'after the delete';
  env.window.sendMessage();
  socket.receive({ type: 'tool_call', name: 'read', arguments: { file_path: 'b.py' }, id: 't2' });
  socket.receive({ type: 'tool_result', id: 't2', output: 'after delete' });

  const block = workBlock(env.document);
  assert.ok(block, 'the work block is rebuilt, not left orphaned');
  assert.ok(block.isConnected, 'the rebuilt work block is attached to the document');
  assert.ok(
    stepOutputs(steps(env.document)[0]).some((o) => o.includes('after delete')),
    'the new step renders its tool output'
  );
});

// ── Step numbering across sessions ────────────────────────────────────────────
// workStepCount is page-level state, but it numbers the CURRENT flow's steps.
// It used to be reset only in loadMessages(), so createSession() -- which wipes
// #messages via showWelcome() without loading messages -- resumed the count from
// the previous session: a fresh session's first step rendered as "Step 42".

test('a new session restarts work step numbering at 1', async () => {
  const env = await boot({
    fixtures: {
      '/api/sessions': (method) =>
        method === 'POST' ? { id: 'session-2' } : [{ id: 'session-1', name: 'one' }],
    },
  });
  startTurn(env);
  await turnWithOneTool(env);
  env.feed({ type: 'text_delta', content: 'again' });
  env.feed({ type: 'tool_call', name: 'grep', arguments: { pattern: 'z' }, id: 't2' });
  env.feed({ type: 'tool_result', id: 't2', output: 'second' });
  env.feed({ type: 'done' });
  assert.strictEqual(
    workBlock(env.document).querySelector('.work-count').textContent,
    '2',
    'precondition: the old session counted two steps'
  );

  await env.window.createSession();
  await new Promise((r) => setTimeout(r, 20));
  const socket = env.sockets[env.sockets.length - 1];

  env.window.document.getElementById('user-input').value = 'fresh session';
  env.window.sendMessage();
  socket.receive({ type: 'tool_call', name: 'read', arguments: { file_path: 'b.py' }, id: 't3' });
  socket.receive({ type: 'tool_result', id: 't3', output: 'fresh' });

  const [first] = steps(env.document);
  assert.strictEqual(first.dataset.n, '1', 'the first step of a new session is Step 1');
  assert.ok(
    first.querySelector('.work-step-header').textContent.includes('Step 1'),
    'the header reads "Step 1", not a carried-over number'
  );
  assert.strictEqual(
    workBlock(env.document).querySelector('.work-count').textContent,
    '1',
    'the Work header counts only this session\'s steps'
  );
});

// ── Multi-turn placement ─────────────────────────────────────────────────────
// Regression: ensureWorkBlock() cached a single container and only ever anchored
// it once, so every later turn's steps were appended under the first prompt.
// The live container also stayed where the first tool call put it, making the
// current turn's work appear up with the old work instead of near its prompt.

test('each live turn gets its own work block under its prompt', async () => {
  const env = await boot();
  startTurn(env, 'first');
  env.feed({ type: 'tool_call', name: 'read', arguments: { file_path: 'a.py' }, id: 't1' });
  env.feed({ type: 'tool_result', id: 't1', output: 'one' });
  env.feed({ type: 'text_delta', content: 'First done.' });
  env.feed({ type: 'done' });

  startTurn(env, 'second');
  env.feed({ type: 'tool_call', name: 'read', arguments: { file_path: 'b.py' }, id: 't2' });
  env.feed({ type: 'tool_result', id: 't2', output: 'two' });
  env.feed({ type: 'text_delta', content: 'Second done.' });
  env.feed({ type: 'done' });

  const blocks = Array.from(env.document.querySelectorAll('.work-block'));
  assert.strictEqual(blocks.length, 2, 'a second turn must not append to the first block');
  assert.deepStrictEqual(stepOutputs(blocks[0]), ['one'], 'the first turn keeps its own step');
  assert.deepStrictEqual(stepOutputs(blocks[1]), ['two'], 'the second turn keeps its own step');

  const users = Array.from(env.document.querySelectorAll('.message'))
    .filter((el) => el.querySelector('.message-role.user'));
  const FOLLOWING = env.window.Node.DOCUMENT_POSITION_FOLLOWING;
  assert.ok(users[0].compareDocumentPosition(blocks[0]) & FOLLOWING, 'block one sits under prompt one');
  assert.ok(users[1].compareDocumentPosition(blocks[1]) & FOLLOWING, 'block two sits under prompt two');
  assert.ok(blocks[0].compareDocumentPosition(users[1]) & FOLLOWING, 'block one stays above prompt two');
});

test('a reloaded multi-turn session groups each turn under its own prompt', async () => {
  const TWO_TURNS = [
    { id: 'u1', role: 'user', content: 'first' },
    {
      id: 'a1', role: 'assistant', content: '',
      tool_calls: [{ id: 't1', type: 'function', function: { name: 'read', arguments: '{}' } }],
    },
    { id: 'r1', role: 'tool', content: 'one', tool_call_id: 't1' },
    { id: 'a2', role: 'assistant', content: 'first done' },
    { id: 'u2', role: 'user', content: 'second' },
    {
      id: 'a3', role: 'assistant', content: '',
      tool_calls: [{ id: 't2', type: 'function', function: { name: 'read', arguments: '{}' } }],
    },
    { id: 'r2', role: 'tool', content: 'two', tool_call_id: 't2' },
    { id: 'a4', role: 'assistant', content: 'second done' },
  ];
  const env = await boot({
    fixtures: { '/api/sessions/session-1/messages': TWO_TURNS },
  });

  const blocks = Array.from(env.document.querySelectorAll('.work-block'));
  assert.strictEqual(blocks.length, 2, 'reload must not stack every step in one block');
  assert.deepStrictEqual(stepOutputs(blocks[0]), ['one']);
  assert.deepStrictEqual(stepOutputs(blocks[1]), ['two']);

  const users = Array.from(env.document.querySelectorAll('.message'))
    .filter((el) => el.querySelector('.message-role.user'));
  const FOLLOWING = env.window.Node.DOCUMENT_POSITION_FOLLOWING;
  assert.ok(users[0].compareDocumentPosition(blocks[0]) & FOLLOWING, 'block one follows prompt one');
  assert.ok(blocks[0].compareDocumentPosition(users[1]) & FOLLOWING, 'block one precedes prompt two');
  assert.ok(users[1].compareDocumentPosition(blocks[1]) & FOLLOWING, 'block two follows prompt two');
});

test('a compaction notice stays with the current turn instead of the flow bottom', async () => {
  const env = await boot();
  startTurn(env, 'compact me');
  env.feed({ type: 'tool_call', name: 'read', arguments: { file_path: 'a.py' }, id: 't1' });
  env.feed({ type: 'tool_result', id: 't1', output: 'one' });
  env.feed({ type: 'compacted', message: 'Context window compressed to make room' });
  env.feed({ type: 'text_delta', content: 'Answer after compaction.' });
  env.feed({ type: 'done' });

  const notice = env.document.querySelector('.compaction-notice');
  assert.ok(notice, 'the notice is rendered');
  assert.ok(!notice.classList.contains('system-error'), 'compaction is not an error');

  const user = env.document.querySelector('.message-role.user').closest('.message');
  const response = Array.from(env.document.querySelectorAll('.message-role.assistant'))
    .map((role) => role.closest('.message'))
    .pop();
  assert.ok(response, 'precondition: the response rendered');
  const FOLLOWING = env.window.Node.DOCUMENT_POSITION_FOLLOWING;
  assert.ok(user.compareDocumentPosition(notice) & FOLLOWING, 'notice sits below the prompt');
  assert.ok(notice.compareDocumentPosition(response) & FOLLOWING, 'notice sits above the response');
});
