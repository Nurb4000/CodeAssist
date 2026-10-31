'use strict';
// The chat gauge reports per-request *window* usage. This covers the separate
// session-budget line (§13 #16b): how close the cumulative session budget is to
// its reset point, fed alongside the window `context` event.

const test = require('node:test');
const assert = require('node:assert');
const { boot, startTurn } = require('./harness.js');

const CONFIG = {
  model: 'test-model',
  detected_model: 'test-model',
  effective_model: 'test-model',
  provider: 'test',
  workspace: '/tmp/ws',
  agent_name: 'default',
  vision: false,
  vision_capable: false,
  context_window: 1000,
  effective_context_window: 1000,
  detected_context_window: 1000,
  backend_source: null,
  backend_external: false,
  compaction_threshold_pct: 75,
  features: {},
};

const budgetContext = (over) => ({
  type: 'context',
  tokens: over ? 850 : 400,
  usage_pct: over ? 85 : 40,
  severity: over ? 'warning' : 'ok',
  budget_enabled: true,
  budget_pct: over ? 85 : 42.5,
  budget_max_tokens: 1000,
  budget_threshold_pct: 80,
});

test('no budget line when the context event omits budget state', async () => {
  const env = await boot({ fixtures: { '/api/config': CONFIG } });
  startTurn(env, 'hello');
  env.feed({ type: 'context', tokens: 100, usage_pct: 10, severity: 'ok' });

  assert.strictEqual(
    env.document.getElementById('budget-usage'),
    null,
    'a window-only update must not create a budget line',
  );
});

test('a capped, enabled budget renders its own line under the window gauge', async () => {
  const env = await boot({ fixtures: { '/api/config': CONFIG } });
  startTurn(env, 'hello');
  env.feed(budgetContext(false));

  const el = env.document.getElementById('budget-usage');
  assert.ok(el, 'the budget line is created');
  assert.match(el.textContent, /Session budget/);
  assert.match(el.textContent, /42\.5%/);
  assert.match(el.textContent, /of 1,000 tokens/);
  assert.match(el.textContent, /resets at 80%/);
});

test('the line turns yellow once usage reaches the rollover threshold', async () => {
  const env = await boot({ fixtures: { '/api/config': CONFIG } });
  startTurn(env, 'hello');
  env.feed(budgetContext(true));

  const span = env.document.querySelector('#budget-usage span');
  assert.ok(span, 'the percentage is colour-coded');
  assert.match(span.getAttribute('style'), /yellow/);
});

test('the line is removed when budgeting stops being enabled', async () => {
  const env = await boot({ fixtures: { '/api/config': CONFIG } });
  startTurn(env, 'hello');
  env.feed(budgetContext(false));
  assert.ok(env.document.getElementById('budget-usage'), 'line exists while enabled');

  env.feed({
    type: 'context',
    tokens: 500,
    usage_pct: 50,
    severity: 'warning',
    budget_enabled: false,
  });

  assert.strictEqual(
    env.document.getElementById('budget-usage'),
    null,
    'a disabled budget clears the line',
  );
});
