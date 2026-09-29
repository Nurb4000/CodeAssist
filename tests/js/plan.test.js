'use strict';
// Behavioural tests for the plan (task list) panel.
//
// Regression coverage for: switching sessions destroyed the plan. The panel used
// to POST /api/todos/clear on every switch, and the plan itself lived only in
// process memory on a single shared tool instance, so it was wiped on every
// switch and lost on restart. Plans are now persisted per session, and switching
// loads the new session's plan instead of clearing anything.

const test = require('node:test');
const assert = require('node:assert');
const { boot, startTurn } = require('./harness.js');

// Read the content span, not the whole item: an item's textContent also picks up
// the status glyph from its .plan-checkbox child.
const planItems = (doc) =>
  Array.from(doc.querySelectorAll('#plan-display .plan-item span')).map((n) => n.textContent.trim());

const planStatuses = (doc) =>
  Array.from(doc.querySelectorAll('#plan-display .plan-item')).map((n) =>
    (n.className.match(/plan-item (\w+)/) || [])[1]
  );

// Two sessions, each with its own plan. Keyed by exact URL because the app asks
// for /api/todos?session_id=...
const TWO_PLANS = {
  '/api/sessions': [{ id: 'session-1', name: 'one' }, { id: 'session-2', name: 'two' }],
  '/api/sessions/session-1/messages': [],
  '/api/sessions/session-2/messages': [],
  '/api/todos?session_id=session-1': {
    tasks: [
      { id: 1, content: 'First task', status: 'in_progress' },
      { id: 2, content: 'Second task', status: 'pending' },
    ],
  },
  '/api/todos?session_id=session-2': {
    tasks: [{ id: 1, content: 'Other session work', status: 'pending' }],
  },
};

const EMPTY_SESSION_FIXTURES = {
  '/api/sessions': [{ id: 'session-1', name: 'one' }, { id: 'session-2', name: 'two' }],
  '/api/sessions/session-1/messages': [],
  '/api/sessions/session-2/messages': [],
  '/api/todos?session_id=session-1': { tasks: [] },
  '/api/todos?session_id=session-2': { tasks: [] },
};

test('the plan for the current session is shown on load', async () => {
  const env = await boot({ fixtures: TWO_PLANS });
  assert.deepStrictEqual(planItems(env.document), ['First task', 'Second task']);
  assert.deepStrictEqual(planStatuses(env.document), ['in_progress', 'pending']);
});

test('switching sessions shows that session\'s plan instead of wiping it', async () => {
  const env = await boot({ fixtures: TWO_PLANS });
  assert.deepStrictEqual(planItems(env.document), ['First task', 'Second task']);

  await env.window.switchSession('session-2');
  await new Promise((r) => setTimeout(r, 20));
  assert.deepStrictEqual(
    planItems(env.document), ['Other session work'],
    "the new session's own plan is shown"
  );

  await env.window.switchSession('session-1');
  await new Promise((r) => setTimeout(r, 20));
  assert.deepStrictEqual(
    planItems(env.document), ['First task', 'Second task'],
    "the first session's plan was not destroyed by the round trip"
  );
});

test('switching sessions never asks the server to clear the plan', async () => {
  // The old code POSTed /api/todos/clear on every switch, which erased the
  // plan of whichever session you had been working in.
  const env = await boot({ fixtures: TWO_PLANS });
  await env.window.switchSession('session-2');
  await new Promise((r) => setTimeout(r, 20));

  const clears = env.requests.filter((u) => u.includes('/api/todos/clear'));
  assert.strictEqual(clears.length, 0, 'no clear request was sent while switching');
  assert.deepStrictEqual(planItems(env.document), ['Other session work']);
});

test('switching to a session with no plan clears the panel', async () => {
  // loadTodos() used to only render when the response was non-empty, so moving
  // to a session with no plan left the previous session's plan on screen.
  const env = await boot({ fixtures: TWO_PLANS });
  assert.strictEqual(planItems(env.document).length, 2);

  const emptyEnv = await boot({ fixtures: EMPTY_SESSION_FIXTURES });
  await emptyEnv.window.switchSession('session-2');
  await new Promise((r) => setTimeout(r, 20));
  assert.deepStrictEqual(planItems(emptyEnv.document), []);
});

test('a plan_update from a running turn replaces the panel', async () => {
  const env = await boot({ fixtures: EMPTY_SESSION_FIXTURES });
  assert.deepStrictEqual(planItems(env.document), []);

  startTurn(env);
  env.feed({
    type: 'plan_update',
    tasks: [
      { id: 1, content: 'Investigate the bug', status: 'in_progress' },
      { id: 2, content: 'Write the test', status: 'pending' },
    ],
  });

  assert.deepStrictEqual(planItems(env.document), ['Investigate the bug', 'Write the test']);
  assert.deepStrictEqual(planStatuses(env.document), ['in_progress', 'pending']);
});

test('a plan emptied by the model clears the panel', async () => {
  const env = await boot({ fixtures: TWO_PLANS });
  assert.strictEqual(planItems(env.document).length, 2);

  startTurn(env);
  env.feed({ type: 'plan_update', tasks: [] });

  assert.deepStrictEqual(planItems(env.document), []);
});
