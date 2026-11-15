'use strict';
// The sidebar footer shows a live generation rate while a completion streams,
// estimated from the streamed output (chars / 4) because local/self-hosted
// backends omit token usage. The old cumulative "0 tokens" counter was removed:
// it never worked on usage-less backends and was confused with the context gauge.

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

// Enough characters to cross the repaint threshold (STREAM_RATE_PAINT_CHARS).
const CHUNK = 'x'.repeat(20);

test('no rate line while idle', async () => {
  const env = await boot({ fixtures: { '/api/config': CONFIG } });
  startTurn(env, 'hello');

  assert.strictEqual(
    env.document.getElementById('token-info'),
    null,
    'nothing is rendered until the model streams',
  );
});

test('a live rate appears while prose streams', async () => {
  const env = await boot({ fixtures: { '/api/config': CONFIG } });
  startTurn(env, 'hello');
  env.feed({ type: 'text_delta', content: CHUNK });

  const el = env.document.getElementById('token-info');
  assert.ok(el, 'the rate line is created on the first deltas');
  assert.strictEqual(el.style.display, 'block', 'it is visible while streaming');
  assert.match(el.textContent, /Generating · ~[\d.]+ tok\/s/);
});

test('reasoning deltas count toward the rate too', async () => {
  const env = await boot({ fixtures: { '/api/config': CONFIG } });
  startTurn(env, 'hello');
  env.feed({ type: 'reasoning', content: CHUNK });

  const el = env.document.getElementById('token-info');
  assert.ok(el, 'reasoning-only turns still show a rate');
  assert.match(el.textContent, /tok\/s/);
});

test('finish hides the rate line', async () => {
  const env = await boot({ fixtures: { '/api/config': CONFIG } });
  startTurn(env, 'hello');
  env.feed({ type: 'text_delta', content: CHUNK });
  assert.ok(env.document.getElementById('token-info'), 'shown while streaming');

  env.feed({ type: 'finish', reason: 'stop', usage: { prompt_tokens: 10, completion_tokens: 5 } });

  const el = env.document.getElementById('token-info');
  assert.strictEqual(el.style.display, 'none', 'hidden once the completion ends');
  assert.strictEqual(el.textContent, '', 'and cleared, so no stale number lingers');
});

test('a finished run hides the rate line', async () => {
  const env = await boot({ fixtures: { '/api/config': CONFIG } });
  startTurn(env, 'hello');
  env.feed({ type: 'text_delta', content: CHUNK });

  env.feed({ type: 'done' });

  const el = env.document.getElementById('token-info');
  assert.strictEqual(el.style.display, 'none', 'setBusy(false) clears the rate');
});
