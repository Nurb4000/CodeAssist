'use strict';
// Regression coverage for §9: the session-switch race.
//
// switchSession() used to be a plain async function with no serialization. Two
// overlapping switches (a rapid double-click, or an import that switches right
// after a manual one) would each run loadMessages() against #messages. Because
// loadMessages() reads `currentSessionId` when it builds the fetch URL and at
// render time, an earlier switch whose response is still in flight (real network
// latency) renders its transcript *after* a later switch has already drawn its
// own -- so the final #messages can belong to a different session than
// currentSessionId, and both switches call connectWS(), closing each other's
// socket.
//
// The fix makes switchSession() synchronous: it captures the target id up front
// and chains runSwitch(id) onto a module-level promise queue. runSwitch()
// re-checks staleness (id !== currentSessionId, no turn started) before and after
// every await, so only the final switch loads + connects.
//
// This test forces an earlier switch's /messages response to resolve on a LATER
// macrotask -- after a later switch has already rendered -- using real setTimeout
// gaps (microtask ordering alone is not enough to make the in-flight window
// deterministic).

const test = require('node:test');
const assert = require('node:assert');
const { boot } = require('./harness.js');

// Distinct assistant prose per session so we can tell which transcript won.
let s1Messages = []; // resolved at boot (empty) so the page loads cleanly
let s2Messages = [];

const FIXTURES = {
  '/api/sessions': [
    { id: 'session-1', name: 'one' },
    { id: 'session-2', name: 'two' },
  ],
  // The harness reads `routes` live on each fetch, so mutating these variables
  // between calls changes what the endpoint returns -- including a still-pending
  // promise, which is how we make session-1's reload resolve late.
  '/api/sessions/session-1/messages': () => s1Messages,
  '/api/sessions/session-2/messages': () => s2Messages,
  '/api/todos': [],
};

const gap = (ms = 30) => new Promise((r) => setTimeout(r, ms));

const transcript = (env) =>
  Array.from(env.document.querySelectorAll('#messages .message-content'))
    .map((c) => c.textContent)
    .join('\n');

test('a late-resolving earlier switch does not clobber the latest session', async () => {
  const env = await boot({ fixtures: FIXTURES });

  // Make session-1's reload deferred so its /messages response stays in flight.
  let resolve1;
  const p1 = new Promise((r) => { resolve1 = r; });
  s1Messages = p1;

  // First switch to session-1: issues its fetch against session-1's URL, then
  // hangs on the deferred response. Give it a macrotask so this is genuinely
  // in flight (not just pending microtasks) before we switch away.
  env.window.switchSession('session-1');
  await gap();

  // Second switch to session-2 happens while session-1's response is still
  // outstanding: it renders fast and connects its socket.
  s2Messages = [
    { role: 'user', content: 'Q2' },
    { role: 'assistant', content: 'A2-session-2' },
  ];
  const s2 = env.window.switchSession('session-2');
  await gap();

  // Now session-1's late response arrives.
  resolve1([
    { role: 'user', content: 'Q1' },
    { role: 'assistant', content: 'A1-session-1' },
  ]);
  await Promise.all([s2]);
  await gap();

  const text = transcript(env);
  assert.ok(text.includes('A2-session-2'), 'session-2 transcript is rendered');
  assert.ok(!text.includes('A1-session-1'),
    'the late session-1 response must not clobber the latest session');

  // The live socket must belong to session-2, not session-1.
  const last = env.sockets[env.sockets.length - 1];
  assert.ok(last.url.includes('session-2'),
    'the last socket opened is for session-2');
});

test('rapid back-to-back switches serialize to the last target', async () => {
  const env = await boot({ fixtures: FIXTURES });

  // Two clicks with no await between them (a double-click). Both targets have
  // messages so we can see which one the UI finally shows.
  s1Messages = [
    { role: 'user', content: 'Q1' },
    { role: 'assistant', content: 'A1-session-1' },
  ];
  s2Messages = [
    { role: 'user', content: 'Q2' },
    { role: 'assistant', content: 'A2-session-2' },
  ];

  const s1 = env.window.switchSession('session-1');
  const s2 = env.window.switchSession('session-2');
  await Promise.all([s1, s2]);
  await gap();

  const text = transcript(env);
  assert.ok(text.includes('A2-session-2'), 'the last-clicked session wins');
  assert.ok(!text.includes('A1-session-1'), 'the earlier click does not linger');
  assert.ok(
    env.sockets[env.sockets.length - 1].url.includes('session-2'),
    'the live socket is session-2',
  );
});
