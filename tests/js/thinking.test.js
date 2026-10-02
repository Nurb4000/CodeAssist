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
//   3. loadMessages()/showWelcome() wipe #messages but left pastThinkingEl
//      pointing at the detached container, so every later archive went into an
//      orphan subtree. That is why re-entering a session showed the Work
//      container but no thinking.

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

test('a reasoning-only turn leaves no orphan agent label', async () => {
  // The model can think after its last tool call and narrate nothing. That turn
  // used to flush a message shell holding only the "CodeAssist" role label once
  // the reasoning was filed away.
  const env = await boot();
  startTurn(env);
  env.feed({ type: 'reasoning', content: 'Step reasoning.' });
  env.feed({ type: 'tool_call', name: 'read', arguments: { file_path: 'a.py' }, id: 't1' });
  env.feed({ type: 'tool_result', id: 't1', output: 'x' });
  env.feed({ type: 'reasoning', content: 'Silent final reasoning.' });
  env.feed({ type: 'done' });

  assert.strictEqual(
    all(env.document, '#messages > .message > .message-role.assistant').length, 0,
    'no assistant message shell may survive without content'
  );
  assert.ok(
    all(env.document, '.past-thinking-entry .thinking-content')
      .some((c) => c.textContent.includes('Silent final reasoning.')),
    'the reasoning is still filed'
  );
});

test('a prose summary keeps its agent label', async () => {
  // Guard against the opposite over-correction: a real answer must not lose it.
  const env = await boot();
  startTurn(env);
  env.feed({ type: 'reasoning', content: 'Reasoning.' });
  env.feed({ type: 'text_delta', content: 'The answer.' });
  env.feed({ type: 'done' });

  const labelled = all(env.document, '#messages > .message').filter((m) =>
    m.querySelector('.message-role.assistant')
  );
  assert.strictEqual(labelled.length, 1, 'the summary message keeps its label');
  assert.ok(
    labelled[0].textContent.includes('The answer.'),
    'and still shows the answer'
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
// ── Container placement ──────────────────────────────────────────────────────
// The reported symptom: "the past thinking container is most often up near the
// original prompt, as expected, but sometimes it sits down just below the
// response." Placement is decided purely by DOM insertion order, and there were
// two anchors: tool-using turns anchored after the Work block (correct), while a
// prose-only turn built no Work block at all and fell through to
// messagesEl.appendChild() -- the very end of the flow, i.e. after the response.
// The two paths were pinned only for existence and content, never for position.

/** Order of #messages' children, as a comparable array of selector matches. */
function flowOrder(doc) {
  return Array.from(doc.querySelector('#messages').children).map((el) => {
    if (el.classList.contains('thinking-history')) return 'past-thinking';
    if (el.classList.contains('work-block')) return 'work';
    if (el.classList.contains('work-active')) return 'work-active';
    if (el.classList.contains('message')) {
      const role = el.querySelector('.message-role');
      return role && role.classList.contains('user') ? 'user' : 'assistant';
    }
    return el.className || el.tagName.toLowerCase();
  });
}

test('a tool-using turn keeps past thinking between the prompt and the response', async () => {
  const env = await boot();
  startTurn(env, 'do the thing');
  env.feed({ type: 'reasoning', content: 'Plan the change.' });
  env.feed({ type: 'tool_call', name: 'edit', arguments: { file_path: 'a.py' }, id: 't1' });
  env.feed({ type: 'tool_result', id: 't1', output: 'ok' });
  env.feed({ type: 'text_delta', content: 'Done.' });
  env.feed({ type: 'done' });

  const order = flowOrder(env.document);
  assert.ok(order.includes('past-thinking'), 'the container exists');
  assert.ok(order.includes('assistant'), 'the turn produced a response');
  const at = order.indexOf('past-thinking');
  assert.ok(
    at > order.indexOf('user') && at < order.indexOf('assistant'),
    `past thinking must sit under the prompt and above the response, got ${JSON.stringify(order)}`
  );
});

test('a prose-only turn keeps past thinking above the response too', async () => {
  // No tool call, so no Work block is ever created (only openWorkStep() and
  // commitPendingUnit() call ensureWorkBlock()). This is the path that used to
  // append the container to the end of #messages.
  const env = await boot();
  startTurn(env, 'just answer me');
  env.feed({ type: 'reasoning', content: 'Reasoning then a prose answer.' });
  env.feed({ type: 'text_delta', content: 'The answer.' });
  env.feed({ type: 'done' });

  const order = flowOrder(env.document);
  assert.ok(order.includes('past-thinking'), 'the container exists');
  const at = order.indexOf('past-thinking');
  assert.ok(
    at > order.indexOf('user') && at < order.indexOf('assistant'),
    `past thinking must sit under the prompt and above the response, got ${JSON.stringify(order)}`
  );
});

test('a reloaded prose-only session keeps past thinking above the response', async () => {
  // Same placement contract for the reload path: flushSummary() renders the
  // assistant message *before* it files the reasoning, so a container created
  // there had to be anchored explicitly or it landed after that message.
  const PROSE_SESSION = [
    { id: 'u1', role: 'user', content: 'answer without tools' },
    { id: 'a1', role: 'assistant', content: 'The answer.', reasoning_content: 'Quietly thought.' },
  ];
  const env = await boot({
    fixtures: {
      '/api/sessions': [{ id: 'session-1', name: 'one' }],
      '/api/sessions/session-1/messages': PROSE_SESSION,
    },
  });

  const order = flowOrder(env.document);
  assert.ok(order.includes('past-thinking'), 'the container exists');
  const at = order.indexOf('past-thinking');
  assert.ok(
    at > order.indexOf('user') && at < order.indexOf('assistant'),
    `past thinking must sit under the prompt and above the response, got ${JSON.stringify(order)}`
  );
});

// ── Session switches ─────────────────────────────────────────────────────────
// The reported symptom: "if I leave the session then return, the work container
// is there, but not always the thinking." Work survived because loadMessages()
// explicitly nulled the work-block handles; the past-thinking handles were never
// reset, so the next archive landed in a detached node.

const REASONED_SESSION = [
  { id: 'u1', role: 'user', content: 'do the thing' },
  {
    id: 'a1', role: 'assistant', content: '',
    tool_calls: [{ id: 't1', type: 'function', function: { name: 'read', arguments: '{}' } }],
    reasoning_content: 'Deep thought about the task.',
  },
  { id: 'r1', role: 'tool', content: 'x', tool_call_id: 't1' },
  { id: 'a2', role: 'assistant', content: 'The answer.', reasoning_content: 'Wrapping up.' },
];

const TWO_SESSION_FIXTURES = {
  '/api/sessions': [{ id: 'session-1', name: 'one' }, { id: 'session-2', name: 'two' }],
  '/api/sessions/session-1/messages': REASONED_SESSION,
  '/api/sessions/session-2/messages': [],
};

const reasoningInDocument = (doc) =>
  all(doc, '.thinking-content').map((c) => c.textContent).join(' ');

test('a session restored on load files its reasoning into the document', async () => {
  // Baseline: with no session switch involved, the reload path must file
  // reasoning into a live (connected) container.
  const env = await boot({ fixtures: TWO_SESSION_FIXTURES });
  const doc = env.document;
  assert.strictEqual(all(doc, '.past-thinking-entry').length, 2, 'both reasonings filed');
  assert.ok(
    q(doc, '.thinking-history').isConnected,
    'the past-thinking container must be attached to the document'
  );
  assert.ok(
    reasoningInDocument(doc).includes('Deep thought about the task.'),
    'step reasoning is reachable in the document'
  );
});

test('re-entering a session restores its thinking (not just the work block)', async () => {
  const env = await boot({ fixtures: TWO_SESSION_FIXTURES });

  // Leave for another session, then come back.
  await env.window.switchSession('session-2');
  await new Promise((r) => setTimeout(r, 20));
  await env.window.switchSession('session-1');
  await new Promise((r) => setTimeout(r, 20));

  const doc = env.document;
  const container = q(doc, '.thinking-history');
  assert.ok(container, 'the past-thinking container is rebuilt after a session switch');
  assert.ok(
    container.isConnected,
    'the rebuilt container must be attached to the document, not left orphaned'
  );
  assert.strictEqual(
    all(doc, '.work-step').length, 1, 'the work step is restored too'
  );
  assert.strictEqual(
    all(doc, '.past-thinking-entry').length, 2,
    'both reasonings are filed after re-entering the session'
  );
  assert.ok(
    reasoningInDocument(doc).includes('Deep thought about the task.'),
    'step reasoning is visible again'
  );
  assert.ok(
    reasoningInDocument(doc).includes('Wrapping up.'),
    'summary reasoning is visible again'
  );
});

test('the past-thinking counter restarts instead of accumulating across switches', async () => {
  const env = await boot({ fixtures: TWO_SESSION_FIXTURES });
  await env.window.switchSession('session-2');
  await new Promise((r) => setTimeout(r, 20));
  await env.window.switchSession('session-1');
  await new Promise((r) => setTimeout(r, 20));

  const count = q(env.document, '.thinking-history .past-thinking-count');
  assert.ok(count, 'the container shows a count');
  assert.strictEqual(
    count.textContent, '2',
    'the count reflects this session\'s entries, not a running total across visits'
  );
});

test('thinking produced after a session switch still lands in the document', async () => {
  // The live path, not the reload path: archiveThinking() must not write into a
  // container orphaned by an earlier switch.
  const env = await boot({ fixtures: TWO_SESSION_FIXTURES });
  await env.window.switchSession('session-2');
  await new Promise((r) => setTimeout(r, 20));
  await env.window.switchSession('session-1');
  await new Promise((r) => setTimeout(r, 20));

  startTurn(env, 'another turn');
  env.feed({ type: 'reasoning', content: 'Fresh reasoning after switching.' });
  env.feed({ type: 'tool_call', name: 'read', arguments: { file_path: 'b.py' }, id: 't9' });
  env.feed({ type: 'tool_result', id: 't9', output: 'y' });
  env.feed({ type: 'done' });

  assert.ok(
    reasoningInDocument(env.document).includes('Fresh reasoning after switching.'),
    'reasoning streamed after a switch is archived into a live container'
  );
});
