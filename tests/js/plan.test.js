'use strict';
// Behavioural tests for the plan (task list) panel.
//
// Regression coverage for: switching sessions destroyed the plan. The panel used
// to POST /api/todos/clear on every switch, and the plan itself lived only in
// process memory on a single shared tool instance, so it was wiped on every
// switch and lost on restart. Plans are now persisted per session, and switching
// loads the new session's plan instead of clearing anything.
//
// And for: a plan longer than the panel's ~6-row box was cut off after the sixth
// task. The renderer sliced the task list to fit, so the tasks past the fold were
// never in the DOM -- there was nothing for the scroll box to scroll and no way
// to read a status that sat below it. All of them render now, and the box follows
// the active task so the next pending one is on screen as work completes.

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

// One task row's height, as app.js assumes it when there is no layout to measure
// (jsdom reports 0 for every box, so the fallback is what runs here).
const ROW = 27;
// Rows the panel shows before it scrolls, matching .plan-list's max-height.
const BOX_ROWS = 6;

const planList = (doc) => doc.querySelector('#plan-display .plan-list');

/**
 * The rows a reader can actually see, top to bottom.
 *
 * jsdom has no layout, so the box height is derived from the same constant
 * app.js anchors against rather than measured.
 */
const visibleRows = (doc) => {
  const list = planList(doc);
  if (!list) return [];
  const items = Array.from(list.querySelectorAll('.plan-item'));
  const first = Math.floor(list.scrollTop / ROW);
  return items.slice(first, first + BOX_ROWS);
};

const visibleItems = (doc) => visibleRows(doc).map((n) => n.querySelector('span').textContent.trim());

/** A plan of `n` tasks, all pending except the one at `activeIndex`. */
const longPlan = (n, activeIndex) =>
  Array.from({ length: n }, (_, i) => ({
    id: i + 1,
    content: `Task ${i + 1}`,
    status: i === activeIndex ? 'in_progress' : 'pending',
  }));

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

test('every task is rendered, not just the six that fit the box', async () => {
  // The renderer used to slice the task list to the box height, so anything past
  // the sixth row existed nowhere: no row, no status, nothing to scroll to.
  const env = await boot({
    fixtures: {
      '/api/sessions': [{ id: 'session-1', name: 'one' }],
      '/api/sessions/session-1/messages': [],
      '/api/todos?session_id=session-1': { tasks: longPlan(9, 2) },
    },
  });

  assert.strictEqual(planItems(env.document).length, 9, 'all nine tasks are in the DOM');
  assert.deepStrictEqual(
    planStatuses(env.document),
    ['pending', 'pending', 'in_progress', 'pending', 'pending', 'pending', 'pending', 'pending', 'pending']
  );
  assert.ok(planList(env.document), 'the scrolling box is rendered');
});

test('a plan taller than the box opens on the active task', async () => {
  const env = await boot({
    fixtures: {
      '/api/sessions': [{ id: 'session-1', name: 'one' }],
      '/api/sessions/session-1/messages': [],
      '/api/todos?session_id=session-1': { tasks: longPlan(9, 7) },
    },
  });

  assert.ok(
    visibleItems(env.document).includes('Task 8'),
    `the task in progress is on screen, saw ${JSON.stringify(visibleItems(env.document))}`
  );
  assert.ok(visibleItems(env.document).includes('Task 7'), 'a completed row above it gives context');
});

test('completing a task scrolls the panel on to the next one', async () => {
  const env = await boot({
    fixtures: {
      '/api/sessions': [{ id: 'session-1', name: 'one' }],
      '/api/sessions/session-1/messages': [],
      '/api/todos?session_id=session-1': { tasks: longPlan(9, 7) },
    },
  });
  const before = planList(env.document).scrollTop;

  startTurn(env);
  env.feed({
    type: 'plan_update',
    tasks: longPlan(9, 8).map((t, i) => (i === 7 ? { ...t, status: 'completed' } : t)),
  });

  assert.ok(
    planList(env.document).scrollTop > before,
    `the window advanced down the list (was ${before}, now ${planList(env.document).scrollTop})`
  );
  assert.ok(
    visibleItems(env.document).includes('Task 9'),
    'the newly active task is on screen without scrolling'
  );
});

test('the next pending task is followed when nothing is in progress', async () => {
  const env = await boot({
    fixtures: {
      '/api/sessions': [{ id: 'session-1', name: 'one' }],
      '/api/sessions/session-1/messages': [],
      '/api/todos?session_id=session-1': { tasks: longPlan(9, -1) },
    },
  });

  assert.ok(visibleItems(env.document).includes('Task 1'), 'the first pending task is on screen');
  assert.strictEqual(planList(env.document).scrollTop, 0, 'the top of the list is already the right view');
});

test('a plan that fits the box is not scrolled', async () => {
  const env = await boot({
    fixtures: {
      '/api/sessions': [{ id: 'session-1', name: 'one' }],
      '/api/sessions/session-1/messages': [],
      '/api/todos?session_id=session-1': { tasks: longPlan(6, 5) },
    },
  });

  assert.strictEqual(planList(env.document).scrollTop, 0);
  assert.deepStrictEqual(visibleItems(env.document), [
    'Task 1', 'Task 2', 'Task 3', 'Task 4', 'Task 5', 'Task 6',
  ]);
});

test('an update that leaves the active task alone does not move the list', async () => {
  const env = await boot({
    fixtures: {
      '/api/sessions': [{ id: 'session-1', name: 'one' }],
      '/api/sessions/session-1/messages': [],
      '/api/todos?session_id=session-1': { tasks: longPlan(9, 7) },
    },
  });

  // Someone scrolls back up through the finished work to read it.
  const list = planList(env.document);
  list.scrollTop = 0;

  startTurn(env);
  env.feed({
    type: 'plan_update',
    tasks: longPlan(9, 7).map((t, i) => (i === 0 ? { ...t, status: 'completed' } : t)),
  });

  assert.strictEqual(planList(env.document).scrollTop, 0, 'their position survived the re-render');
});

test('the count badge reports progress for a long plan', async () => {
  const env = await boot({
    fixtures: {
      '/api/sessions': [{ id: 'session-1', name: 'one' }],
      '/api/sessions/session-1/messages': [],
      '/api/todos?session_id=session-1': { tasks: longPlan(9, -1) },
    },
  });

  const count = env.document.querySelector('#plan-display .plan-count');
  assert.strictEqual(count.textContent, '9', 'the total is on the title, not just what fits');
  assert.match(count.title, /9 tasks/);
});

test('switching sessions does not carry the old session scroll position', async () => {
  const env = await boot({
    fixtures: {
      '/api/sessions': [{ id: 'session-1', name: 'one' }, { id: 'session-2', name: 'two' }],
      '/api/sessions/session-1/messages': [],
      '/api/sessions/session-2/messages': [],
      '/api/todos?session_id=session-1': { tasks: longPlan(9, 8) },
      '/api/todos?session_id=session-2': { tasks: longPlan(9, 0) },
    },
  });
  assert.ok(planList(env.document).scrollTop > 0, 'the first plan is scrolled to its active task');

  await env.window.switchSession('session-2');
  await new Promise((r) => setTimeout(r, 20));

  assert.strictEqual(
    planList(env.document).scrollTop, 0,
    "the second session's panel starts at its own active task, not the first one's offset"
  );
});
