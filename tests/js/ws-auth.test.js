'use strict';
// Tests for WebSocket server-password authentication (review item §1.11).
//
// The server authenticates the socket via the Sec-WebSocket-Protocol header
// (server.py): when a password is configured, a connection without the matching
// subprotocol is closed with code 4001. The client can never learn the password
// from the server, so it must let the user supply it once, store it, and send it
// as the subprotocol on connect. Before this fix the only reaction to 4001 was
// "Connection error" with no way to ever authenticate -- the browser UI could
// not connect at all when a password was set.

const test = require('node:test');
const assert = require('node:assert');
const { boot } = require('./harness.js');

const sel = (env, id) => env.document.getElementById(id);

test('connects without a subprotocol when no password is stored', async () => {
  const env = await boot();
  // No Sec-WebSocket-Protocol requested -> the socket carries no subprotocol.
  assert.strictEqual(env.socket.protocols, undefined, 'no subprotocol without a password');
});

test('a stored password is sent as the WebSocket subprotocol', async () => {
  const env = await boot({
    preload: (window) => { window.localStorage.setItem('ws-password', 's3cret'); },
  });
  assert.strictEqual(env.socket.protocols, 's3cret', 'the stored password is requested as subprotocol');
});

test('a 4001 close surfaces a password prompt instead of a dead "Connection error"', async () => {
  const env = await boot();

  // Simulate the server closing the socket with the auth-failure code.
  env.socket.onclose({ code: 4001 });

  const prompt = sel(env, 'ws-password-prompt');
  assert.ok(prompt, 'the password prompt is shown after a 4001 close');
  assert.ok(prompt.querySelector('input[type="password"]'), 'the prompt has a password field');
});

test('entering the password in the prompt reconnects with it as subprotocol', async () => {
  const env = await boot();

  env.socket.onclose({ code: 4001 });
  const prompt = sel(env, 'ws-password-prompt');
  assert.ok(prompt, 'precondition: prompt is visible');

  const input = prompt.querySelector('input[type="password"]');
  input.value = 'p@ss:w!rd';
  const connectBtn = [...prompt.querySelectorAll('button')].find((b) => b.classList.contains('primary'));
  connectBtn.dispatchEvent(new env.window.MouseEvent('click', { bubbles: true, cancelable: true }));

  // The reconnect opens a fresh socket carrying the entered password.
  const sockets = env.sockets;
  assert.ok(sockets.length > 1, 'a new socket is opened after authenticating');
  const last = sockets[sockets.length - 1];
  assert.strictEqual(last.protocols, 'p@ss:w!rd', 'the entered password is sent as subprotocol');
  assert.strictEqual(
    env.window.localStorage.getItem('ws-password'),
    'p@ss:w!rd',
    'the password is persisted for future reconnects',
  );
});

test('an empty password in the prompt does not reconnect', async () => {
  const env = await boot();
  env.socket.onclose({ code: 4001 });
  const prompt = sel(env, 'ws-password-prompt');
  const input = prompt.querySelector('input[type="password"]');
  input.value = ''; // still empty
  const connectBtn = [...prompt.querySelectorAll('button')].find((b) => b.classList.contains('primary'));
  connectBtn.dispatchEvent(new env.window.MouseEvent('click', { bubbles: true, cancelable: true }));

  assert.strictEqual(env.sockets.length, 1, 'no reconnect is attempted with an empty password');
});

test('canceling the prompt closes it without reconnecting', async () => {
  const env = await boot();
  env.socket.onclose({ code: 4001 });
  const prompt = sel(env, 'ws-password-prompt');
  assert.ok(prompt, 'precondition: prompt visible');

  const cancelBtn = [...prompt.querySelectorAll('button')].find(
    (b) => !b.classList.contains('primary'),
  );
  cancelBtn.dispatchEvent(new env.window.MouseEvent('click', { bubbles: true, cancelable: true }));

  assert.strictEqual(sel(env, 'ws-password-prompt'), null, 'the prompt is removed on cancel');
  assert.strictEqual(env.sockets.length, 1, 'canceling does not reconnect');
});
