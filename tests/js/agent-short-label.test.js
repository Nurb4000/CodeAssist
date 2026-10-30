'use strict';
// Regression for a §11 MINOR: `AGENT_SHORT` (app.js) mapped every built-in agent
// to its collapsed selector label except `deep_review`, so selecting that agent
// fell through to `agentShortLabel()` returning the raw id ("deep_review")
// instead of a friendly "Deep Review". The map now includes it.

const test = require('node:test');
const assert = require('node:assert');
const { boot } = require('./harness.js');

test('the deep_review agent gets its short label instead of a raw id', async () => {
  const env = await boot({ fixtures: { '/api/agents': [] } });
  const label = env.window.agentShortLabel('deep_review');
  assert.strictEqual(label, 'Deep Review', 'deep_review maps to "Deep Review"');
});

test('every other built-in agent still maps to its short label', async () => {
  const env = await boot({ fixtures: { '/api/agents': [] } });
  const fn = env.window.agentShortLabel;
  assert.strictEqual(fn('default'), 'Full');
  assert.strictEqual(fn('research'), 'Research');
  assert.strictEqual(fn('review'), 'Review');
  assert.strictEqual(fn('build'), 'Build');
  assert.strictEqual(fn('general'), 'General');
  assert.strictEqual(fn('explore'), 'Explore');
});

test('an unknown agent id falls through to the raw id (unchanged behavior)', async () => {
  const env = await boot({ fixtures: { '/api/agents': [] } });
  const fn = env.window.agentShortLabel;
  assert.strictEqual(fn('does-not-exist'), 'does-not-exist');
  assert.strictEqual(fn(''), 'Agent', 'empty id falls back to the default label');
});
