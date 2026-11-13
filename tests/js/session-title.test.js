'use strict';
// Session naming UX:
//  - a manually named session hides the generated summary beneath its title
//  - a model-written title pushed by the server updates the sidebar in place

const test = require('node:test');
const assert = require('node:assert');
const { boot } = require('./harness.js');

const FIXTURES = {
  '/api/sessions': [
    {
      id: 'session-1',
      name: 'Auto title',
      summary: 'Session started with: fix the login bug',
      title_source: 'auto',
    },
    {
      id: 'session-2',
      name: 'My own title',
      summary: 'Session started with: refactor auth',
      title_source: 'manual',
    },
  ],
  '/api/sessions/session-1/messages': [],
  '/api/sessions/session-2/messages': [],
  '/api/todos': [],
};

test('the summary is hidden only for manually titled sessions', async () => {
  const env = await boot({ fixtures: FIXTURES });
  const items = env.document.querySelectorAll('.session-item');

  assert.ok(items[0].querySelector('.session-summary'), 'auto-titled row keeps its summary');
  assert.strictEqual(
    items[1].querySelector('.session-summary'),
    null,
    'manually titled row drops the summary',
  );
});

test('a server-pushed AI title updates the sidebar in place', async () => {
  const env = await boot({ fixtures: FIXTURES });
  const name = env.document.querySelector('.session-item .session-name');

  env.feed({ type: 'session_title', session_id: 'session-1', title: 'Login timeout fix' });

  assert.strictEqual(name.textContent, 'Login timeout fix');
  assert.ok(
    name.closest('.session-item').querySelector('.session-summary'),
    'an AI title is not manual, so its summary stays',
  );
});
