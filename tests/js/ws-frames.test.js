'use strict';
// Regression for the unguarded JSON.parse in ws.onmessage (app.js).
//
// A corrupt or half-sent WebSocket frame reaches JSON.parse(event.data) at the
// top of the message handler. Without a guard that throws an uncaught exception
// out of the handler -- in a browser, reported as a window error and aborting
// dispatch of the frame (and any frames queued behind it on that tick). The
// handler now wraps the parse/dispatch so a bad frame is dropped and later
// frames still process.

const test = require('node:test');
const assert = require('node:assert');
const { boot, startTurn } = require('./harness.js');

test('a malformed WebSocket frame does not throw out of the handler', async () => {
  const env = await boot();
  startTurn(env);
  assert.ok(
    env.document.body.classList.contains('is-working'),
    'precondition: busy during the turn',
  );

  // A non-JSON frame. Without the guard, JSON.parse throws and this call rejects
  // (the async test fails). With the guard it is swallowed and returns normally.
  env.socket.onmessage({ data: 'this is not valid json' });

  // A subsequent valid frame still dispatches after the malformed one.
  env.feed({ type: 'done' });
  assert.ok(
    !env.document.body.classList.contains('is-working'),
    'the turn ends normally after a malformed frame',
  );
});

test('a truncated JSON frame during a turn is dropped, not fatal', async () => {
  const env = await boot();
  startTurn(env);

  // Valid opening brace, no closing -- the classic half-sent reconnect frame.
  env.socket.onmessage({ data: '{"type": "text_delta","content": "' });

  // The turn is still genuinely streaming; a bad frame must not end it or throw.
  assert.ok(
    env.document.body.classList.contains('is-working'),
    'still busy after a truncated frame',
  );

  // A real done arrives afterwards and clears the busy state.
  env.feed({ type: 'done' });
  assert.ok(
    !env.document.body.classList.contains('is-working'),
    'the turn ends once a valid done arrives',
  );
});
