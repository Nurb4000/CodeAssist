'use strict';
// Session naming UX:
//  - the sidebar renders titles only, never the generated session summary
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

test('the sidebar no longer renders generated session summaries', async () => {
  const env = await boot({ fixtures: FIXTURES });
  const items = env.document.querySelectorAll('.session-item');

  assert.strictEqual(items.length, 2);
  for (const item of items) {
    assert.strictEqual(
      item.querySelector('.session-summary'),
      null,
      'no session row should show a summary',
    );
  }
  assert.ok(!env.document.body.textContent.includes('Session started with:'));
});

test('a server-pushed AI title updates the sidebar in place', async () => {
  const env = await boot({ fixtures: FIXTURES });
  const name = env.document.querySelector('.session-item .session-name');

  env.feed({ type: 'session_title', session_id: 'session-1', title: 'Login timeout fix' });

  assert.strictEqual(name.textContent, 'Login timeout fix');
  assert.strictEqual(
    name.closest('.session-item').querySelector('.session-summary'),
    null,
    'AI-titled rows stay summary-free too',
  );
});
