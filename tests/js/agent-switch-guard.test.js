'use strict';
// Regression for §11: the `agent_switched` WS handler called
// `setAgentSelection((data.agent && (data.agent.id || data.agent.name)) || data.agent)`.
// When a malformed event arrived with no `agent`, the trailing `|| data.agent`
// passed `undefined` straight through, so setAgentSelection(undefined) wiped the
// current selection (currentAgentName = undefined and every .mode-option
// un-highlighted). It now guards like `active_agent` and ignores such events.

const test = require('node:test');
const assert = require('node:assert');
const { boot } = require('./harness.js');

const AGENTS = {
  '/api/agents': [
    { id: 'default', name: 'Full' },
    { id: 'research', name: 'Research' },
  ],
};

const option = (env, id) =>
  Array.from(env.document.querySelectorAll('.mode-option')).find((o) => o.dataset.id === id);

test('a malformed agent_switched with no agent preserves the current selection', async () => {
  const env = await boot({ fixtures: AGENTS });

  // Establish a known selection.
  env.feed({ type: 'active_agent', agent: { id: 'research', name: 'Research' } });
  assert.ok(option(env, 'research').classList.contains('active'), 'precondition: research is selected');

  // A malformed event (no `agent`) must not wipe the selection.
  env.feed({ type: 'agent_switched' });

  assert.ok(
    option(env, 'research').classList.contains('active'),
    'the current agent stays selected after a malformed agent_switched'
  );
});

test('a well-formed agent_switched still updates the selection (control)', async () => {
  const env = await boot({ fixtures: AGENTS });

  env.feed({ type: 'active_agent', agent: { id: 'research', name: 'Research' } });
  assert.ok(option(env, 'research').classList.contains('active'), 'precondition: research is selected');

  env.feed({ type: 'agent_switched', agent: { id: 'default', name: 'Full' } });

  assert.ok(option(env, 'default').classList.contains('active'), 'the new agent becomes selected');
  assert.ok(!option(env, 'research').classList.contains('active'), 'the previous agent is no longer active');
});
