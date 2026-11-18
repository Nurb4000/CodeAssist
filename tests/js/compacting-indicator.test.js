'use strict';
// The compaction summary is a silent LLM request: no text deltas, no tool
// calls, not even an assistant placeholder. The UI has to say the wait is
// compaction before it starts, and stop saying it on the next frame -- a
// "compacted" event is deliberately not sent when nothing actually shrank.

const test = require('node:test');
const assert = require('node:assert');
const { boot, startTurn } = require('./harness.js');

const COMPACTING = {
  type: 'compacting',
  message: 'Context window is full — compacting before continuing',
  reason: 'context_window',
  mode: 'llm',
  tokens: 850,
  usage_pct: 85,
};

function indicator(document) {
  return document.getElementById('compacting-status');
}

function progressText(document) {
  const el = document.querySelector('.progress-bar .status-text');
  return el ? el.textContent : '';
}

test('compacting relabels the spinner and adds a line by the context gauge', async () => {
  const env = await boot();
  startTurn(env, 'hello');
  assert.equal(indicator(env.document), null, 'no indicator before compaction');
  assert.match(progressText(env.document), /Thinking/);

  env.feed({ type: 'context', tokens: 850, usage_pct: 85, severity: 'warning' });
  env.feed(COMPACTING);

  const el = indicator(env.document);
  assert.ok(el, 'the sidebar indicator is created');
  assert.match(el.textContent, /compacting before continuing/);
  assert.match(progressText(env.document), /compacting before continuing/);
  assert.ok(el.querySelector('.progress-dots'), 'the indicator animates');
  assert.deepEqual(env.errors, []);
});

test('compacted clears the in-progress state', async () => {
  const env = await boot();
  startTurn(env, 'hello');
  env.feed(COMPACTING);
  assert.ok(indicator(env.document));

  env.feed({
    type: 'compacted',
    message: 'Context window compressed to make room',
    mode: 'llm',
    count: 1,
    tokens_before: 850,
    tokens_after: 300,
  });

  assert.equal(indicator(env.document), null, 'the sidebar line is removed');
  assert.doesNotMatch(progressText(env.document), /compacting/i);
});

test('the next frame clears it when compaction made no progress', async () => {
  const env = await boot();
  startTurn(env, 'hello');
  env.feed(COMPACTING);
  assert.ok(indicator(env.document));

  // No `compacted` event in this path (agent.py only emits it when the request
  // shrank). The follow-up context update must still clear the indicator.
  env.feed({ type: 'context', tokens: 850, usage_pct: 85, severity: 'warning' });

  assert.equal(indicator(env.document), null);
  assert.doesNotMatch(progressText(env.document), /compacting/i);
});

test('back-to-back notices restore the original label, not another compaction label', async () => {
  const env = await boot();
  startTurn(env, 'hello');
  env.feed({ type: 'compacting', message: 'Summarizing the budget checkpoint...' });
  // A window compaction can start in the same loop iteration as a rollover.
  env.feed({ type: 'compacting', message: 'Compacting the context window...' });
  env.feed({ type: 'compacted', message: 'Context window compressed' });

  assert.equal(indicator(env.document), null);
  assert.match(progressText(env.document), /Thinking/);
});

test('terminal events clear it too', async () => {
  for (const terminal of ['done', 'error', 'cancelled']) {
    const env = await boot();
    startTurn(env, 'hello');
    env.feed(COMPACTING);
    assert.ok(indicator(env.document), `${terminal}: indicator shown before the end`);

    env.feed({ type: terminal, message: 'end of turn' });

    assert.equal(indicator(env.document), null, `${terminal}: indicator cleared`);
    assert.deepEqual(env.errors, []);
  }
});
