'use strict';
// Regression for a §9 FRAGILE: reconnects used a fixed 1s/3s delay that looped
// forever with no backoff. Every client retrying at the same instant floods the
// server the moment it recovers (thundering herd), and a persistent outage never
// backs off. computeReconnectDelay() now applies exponential backoff capped at
// 30s with full jitter (a random delay in [capped/2, capped]).

const test = require('node:test');
const assert = require('node:assert');
const { boot } = require('./harness.js');

const RECONNECT_BASE = 3000;
const MAX_DELAY = 30000;
const MAX_ATTEMPTS = 10;

// The deterministic upper bound the jittered delay is guaranteed not to exceed.
function cappedUpperBound(attempt, base) {
  return Math.min(base * 2 ** Math.min(attempt, MAX_ATTEMPTS), MAX_DELAY);
}

test('the delay helper is wired into the module for testing', async () => {
  const env = await boot();
  assert.strictEqual(typeof env.window.computeReconnectDelay, 'function');
});

test('every result lands in [capped/2, capped] across many attempts and jitter draws', async () => {
  const env = await boot();
  const f = env.window.computeReconnectDelay;
  const bases = [1000, 3000];
  for (const base of bases) {
    for (let attempt = 0; attempt <= 15; attempt++) {
      const capped = cappedUpperBound(attempt, base);
      for (let i = 0; i < 25; i++) {
        const d = f(attempt, base);
        assert.ok(
          d >= capped / 2 - 0.5 && d <= capped + 0.5,
          `attempt ${attempt} base ${base}: ${d.toFixed(1)} not in [${capped / 2}, ${capped}]`,
        );
      }
    }
  }
});

test('delay grows monotonically while uncapped (attempts 0..3)', async () => {
  const env = await boot();
  const f = env.window.computeReconnectDelay;
  // While base*2^attempt stays under the cap, each interval's lower bound equals
  // the previous interval's upper bound, so jittered delays cannot decrease.
  let prev = 0;
  for (let attempt = 0; attempt <= 3; attempt++) {
    const d = f(attempt, RECONNECT_BASE);
    assert.ok(d >= prev, `attempt ${attempt} (${d.toFixed(0)}ms) should not shrink below ${prev.toFixed(0)}ms`);
    prev = d;
  }
});

test('the cap holds even for very late attempts', async () => {
  const env = await boot();
  const f = env.window.computeReconnectDelay;
  // Attempt 20 would be base*2^20 without the cap; it must never exceed 30s.
  const d = f(20, RECONNECT_BASE);
  assert.ok(d <= MAX_DELAY + 0.5, `late reconnect ${d.toFixed(0)}ms respects the ${MAX_DELAY}ms cap`);
});

test('the first reconnect on initial load is snappy (within [500ms, 1000ms])', async () => {
  const env = await boot();
  const f = env.window.computeReconnectDelay;
  const d = f(0, 1000);
  assert.ok(d >= 500 && d <= 1000, `initial reconnect ${d.toFixed(0)}ms should be within the snappy window`);
});
