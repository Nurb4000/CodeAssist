'use strict';
// The context gauge must use the provider's real prompt-token count and the
// configured compaction threshold, not only the local estimate and a hardcoded
// 75%. After compaction the estimate can lag; `finish.usage.prompt_tokens` is
// exactly what the request carried.

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
  compaction_threshold_pct: 50,
  features: {},
};

test('finish usage updates the gauge against the effective window', async () => {
  const env = await boot({ fixtures: { '/api/config': CONFIG } });
  startTurn(env, 'hello');
  env.feed({
    type: 'finish',
    reason: 'stop',
    usage: { prompt_tokens: 800, completion_tokens: 10 },
  });

  const el = env.document.getElementById('context-usage');
  assert.ok(el, 'the gauge is created');
  assert.match(el.textContent, /80% context used/);
  assert.match(el.textContent, /800 tokens/);
});

test('the configured threshold decides the warning colour', async () => {
  const env = await boot({ fixtures: { '/api/config': CONFIG } });
  startTurn(env, 'hello');
  // 60% is over the configured 50% trigger, so it must not render as "ok".
  env.feed({
    type: 'finish',
    reason: 'stop',
    usage: { prompt_tokens: 600, completion_tokens: 10 },
  });

  const span = env.document.querySelector('#context-usage span');
  assert.ok(span, 'the coloured percentage is rendered');
  assert.match(span.getAttribute('style'), /yellow/);
});
