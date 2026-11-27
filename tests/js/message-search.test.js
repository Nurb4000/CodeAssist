'use strict';
// B3: in-session full-text search. The panel calls the real REST endpoint with
// the encoded query, renders untrusted message content as DOM nodes (never raw
// HTML), and jumps to a hit -- including tool rows hidden in a collapsed Work
// section. Session switches invalidate stale results and highlights.

const test = require('node:test');
const assert = require('node:assert');
const { boot, startTurn } = require('./harness.js');

const gap = (ms = 30) => new Promise((r) => setTimeout(r, ms));

const MESSAGES = [
  { id: 'u1', role: 'user', content: 'searchable phrase here' },
  { id: 'a1', role: 'assistant', content: 'An answer with needle detail.' },
  {
    id: 'a2',
    role: 'assistant',
    content: '',
    tool_calls: [{ id: 't1', function: { name: 'read', arguments: '{}' } }],
  },
  { id: 'tool1', role: 'tool', tool_call_id: 't1', content: 'needle in tool output' },
];

function fixtures(searchResult = []) {
  return {
    '/api/sessions': [
      { id: 'session-1', name: 'one' },
      { id: 'session-2', name: 'two' },
    ],
    '/api/sessions/session-1/messages': MESSAGES,
    '/api/sessions/session-2/messages': [{ id: 'u2', role: 'user', content: 'other session' }],
    '/api/sessions/session-1/messages/search': searchResult,
    '/api/sessions/session-2/messages/search': [],
  };
}

async function search(env, query) {
  env.document.getElementById('message-search-input').value = query;
  env.document.getElementById('message-search-btn').click();
  await gap();
}

test('the bar opens from the footer button or Ctrl+Shift+F', async () => {
  const env = await boot({ fixtures: fixtures() });
  const panel = env.document.getElementById('message-search');
  assert.equal(panel.hidden, true, 'search starts closed');

  env.document.getElementById('search-messages-btn').click();
  assert.equal(panel.hidden, false, 'footer button opens the search bar');

  env.document.getElementById('message-search-close').click();
  assert.equal(panel.hidden, true, 'close button hides it again');

  env.window.dispatchEvent(new env.window.KeyboardEvent('keydown', {
    key: 'F', ctrlKey: true, shiftKey: true, bubbles: true, cancelable: true,
  }));
  assert.equal(panel.hidden, false, 'Ctrl+Shift+F opens the search bar');
  assert.deepEqual(env.errors, []);
});

test('a query reaches the endpoint encoded and results render with marks', async () => {
  const env = await boot({
    fixtures: fixtures([
      { id: 'a1', role: 'assistant', content: 'An answer with needle detail.' },
    ]),
  });
  await search(env, 'needle word');

  const request = env.requests.find((r) => r.includes('/messages/search?'));
  assert.ok(request, 'the search endpoint was called');
  assert.ok(request.includes('q=needle%20word'), 'the query is URL-encoded');
  assert.match(env.document.getElementById('message-search-status').textContent, /1 result/);
  const marks = env.document.querySelectorAll('.message-search-result-btn mark');
  assert.equal(marks.length, 1, 'the matched term is highlighted');
  assert.equal(marks[0].textContent, 'needle');
  assert.deepEqual(env.errors, []);
});

test('clicking a result scrolls to the stored message and highlights it', async () => {
  const env = await boot({
    fixtures: fixtures([{ id: 'u1', role: 'user', content: 'searchable phrase here' }]),
  });
  await search(env, 'searchable');

  const button = env.document.querySelector('.message-search-result-btn');
  assert.ok(button, 'the result is rendered');
  button.click();

  const anchor = env.document.querySelector('#messages [data-message-id="u1"]');
  assert.ok(anchor, 'replayed messages carry their database id');
  assert.ok(anchor.classList.contains('message-search-hit'), 'the hit is highlighted');
  assert.deepEqual(env.errors, []);
});

test('a tool hit expands the collapsed Work step before highlighting', async () => {
  const env = await boot({
    fixtures: fixtures([
      { id: 'tool1', role: 'tool', tool_call_id: 't1', content: 'needle in tool output' },
    ]),
  });
  assert.ok(env.document.querySelector('.work-block.history-hidden'), 'Work starts collapsed');

  await search(env, 'needle');
  env.document.querySelector('.message-search-result-btn').click();

  assert.equal(env.document.querySelector('.work-block').classList.contains('history-hidden'), false,
    'the Work section is revealed');
  assert.ok(env.document.querySelector('.work-step.open'), 'the step is expanded');
  const toolCall = env.document.querySelector('.tool-call[data-call-id="t1"]');
  assert.equal(toolCall.dataset.messageId, 'tool1', 'the tool row id anchors the call');
  assert.ok(toolCall.classList.contains('message-search-hit'), 'the call is highlighted');
  assert.deepEqual(env.errors, []);
});

test('untrusted message text is rendered as text, not markup', async () => {
  const dangerous = '<img src=x onerror=alert(1)> needle';
  const env = await boot({
    fixtures: fixtures([{ id: 'a1', role: 'assistant', content: dangerous }]),
  });
  await search(env, 'needle');

  const results = env.document.getElementById('message-search-results');
  assert.equal(results.querySelector('img'), null, 'no element from message content is created');
  assert.ok(results.textContent.includes('<img src=x onerror=alert(1)>'), 'the text stays literal');
  assert.equal(results.querySelector('mark').textContent, 'needle');
  assert.deepEqual(env.errors, []);
});

test('an empty query is rejected locally without a request', async () => {
  const env = await boot({ fixtures: fixtures() });
  await search(env, '   ');
  assert.match(env.document.getElementById('message-search-status').textContent, /Enter a word/);
  assert.equal(env.requests.filter((r) => r.includes('/messages/search?')).length, 0);
  assert.deepEqual(env.errors, []);
});

test('Escape clears results and switching sessions invalidates stale hits', async () => {
  const env = await boot({
    fixtures: fixtures([{ id: 'u1', role: 'user', content: 'searchable phrase here' }]),
  });
  await search(env, 'searchable');
  env.document.querySelector('.message-search-result-btn').click();
  assert.ok(env.document.querySelector('.message-search-hit'));

  env.document.getElementById('message-search-input').dispatchEvent(
    new env.window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }),
  );
  assert.equal(env.document.getElementById('message-search').hidden, true);
  assert.equal(env.document.querySelectorAll('.message-search-result').length, 0);
  assert.equal(env.document.querySelectorAll('.message-search-hit').length, 0);

  await search(env, 'searchable');
  env.document.querySelector('.message-search-result-btn').click();
  assert.ok(env.document.querySelector('.message-search-hit'));
  env.window.switchSession('session-2');
  await gap();
  assert.equal(env.document.getElementById('message-search').hidden, true);
  assert.equal(env.document.querySelectorAll('.message-search-result').length, 0);
  assert.equal(env.document.querySelectorAll('.message-search-hit').length, 0);
  assert.deepEqual(env.errors, []);
});

test('a late first response cannot overwrite a newer search', async () => {
  let resolveSlow;
  const slow = new Promise((r) => { resolveSlow = r; });
  let call = 0;
  const env = await boot({
    fixtures: {
      ...fixtures(),
      '/api/sessions/session-1/messages/search': () => (
        ++call === 1
          ? slow
          : [{ id: 'u2', role: 'user', content: 'fast result' }]
      ),
    },
  });

  await search(env, 'slow');
  await search(env, 'fast');
  resolveSlow([{ id: 'u1', role: 'user', content: 'slow result' }]);
  await gap();

  const results = env.document.getElementById('message-search-results').textContent;
  assert.ok(results.includes('fast result'), 'the latest query won');
  assert.ok(!results.includes('slow result'), 'the stale response was ignored');
  assert.deepEqual(env.errors, []);
});

test('search results survive a live turn without breaking the flow layout', async () => {
  const env = await boot({
    fixtures: fixtures([{ id: 'a1', role: 'assistant', content: 'needle detail' }]),
  });
  await search(env, 'needle');
  startTurn(env, 'a new question');
  env.feed({ type: 'text_delta', content: 'Answer.' });
  env.feed({ type: 'done' });
  await gap();

  assert.equal(env.document.querySelectorAll('.message-search-result').length, 1);
  // The panel is a sibling of #messages, so it never becomes a flow child.
  assert.equal(env.document.querySelector('#message-search').parentElement.id, 'chat-area');
  assert.equal(env.document.querySelector('#messages').querySelector('.message-search'), null);
  assert.deepEqual(env.errors, []);
});
