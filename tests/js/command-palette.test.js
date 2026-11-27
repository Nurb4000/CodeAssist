'use strict';
// B1: slash commands and the command palette. Built-ins are local actions and
// must never create a user bubble or a turn. Unknown /name commands are sent
// verbatim so the server can expand them through the skill registry. The palette
// lists built-ins plus slash-enabled skills and keeps skill sending to the user
// so arguments can be typed first.

const test = require('node:test');
const assert = require('node:assert');
const { boot, startTurn } = require('./harness.js');

const gap = (ms = 30) => new Promise((r) => setTimeout(r, ms));

async function runCommand(env, command) {
  env.document.getElementById('user-input').value = command;
  env.window.sendMessage();
  await gap();
}

function userMessages(env) {
  return env.socket.sent.filter((m) => m.type === 'user_message');
}

function key(filter, keyName) {
  filter.dispatchEvent(new filter.ownerDocument.defaultView.KeyboardEvent('keydown', {
    key: keyName, bubbles: true, cancelable: true,
  }));
}

test('typed /help opens help locally instead of sending a turn', async () => {
  const env = await boot();
  await runCommand(env, '/help');

  const help = env.document.querySelector('.command-palette');
  assert.ok(help, 'the help modal opened');
  assert.match(help.textContent, /\/compact/);
  assert.match(help.textContent, /\/undo/);
  assert.match(help.textContent, /\/agent/);
  assert.deepEqual(userMessages(env), [], 'no turn was sent');
  assert.equal(env.document.getElementById('user-input').value, '', 'the command line was cleared');

  help.querySelector('#command-help-close').click();
  assert.equal(env.document.querySelector('.command-palette'), null);
  assert.deepEqual(env.errors, []);
});

test('/undo sends the existing undo frame and reloads after the reply', async () => {
  const env = await boot();
  await runCommand(env, '/undo');

  assert.ok(env.socket.sent.some((m) => m.type === 'undo'), 'the undo frame was sent');
  assert.deepEqual(userMessages(env), [], 'undo is not a user turn');

  env.feed({ type: 'undo_done', deleted: true });
  await gap();
  assert.match(env.document.getElementById('messages').textContent, /Undid the last turn/);
  assert.ok(env.requests.filter((r) => r.includes('/api/sessions/session-1/messages')).length >= 2,
    'the transcript was reloaded');
  assert.deepEqual(env.errors, []);
});

test('/stop is handled during a running turn without sending a second turn', async () => {
  const env = await boot();
  startTurn(env, 'a long question');
  env.feed({ type: 'text_delta', content: 'Still working...' });

  const before = userMessages(env).length;
  await runCommand(env, '/stop');

  assert.equal(userMessages(env).length, before, 'no second user message was queued');
  assert.equal(env.socket.sent.filter((m) => m.type === 'cancel').length, 1, 'one cancel was sent');
  assert.deepEqual(env.errors, []);
});

test('/agent <id> reuses the agent switch frame; /agent alone shows usage', async () => {
  const env = await boot();
  // switchAgent only sends over a connected socket. The keepalive interval is
  // not under test here and would keep Node's event loop alive.
  env.window.setInterval = () => 0;
  env.socket.onopen();
  await runCommand(env, '/agent explore');
  assert.ok(env.socket.sent.some(
    (m) => m.type === 'switch_agent' && m.agent_name === 'explore',
  ), 'the agent switch was sent');

  await runCommand(env, '/agent');
  assert.match(env.document.getElementById('messages').textContent, /Usage: \/agent <id>/);
  assert.deepEqual(userMessages(env), []);
  assert.deepEqual(env.errors, []);
});

test('an unknown slash command is sent verbatim for server-side expansion', async () => {
  const env = await boot();
  await runCommand(env, '/review auth.py');

  assert.equal(userMessages(env).length, 1, 'the command became a normal turn');
  assert.equal(userMessages(env)[0].content, '/review auth.py', 'the text was left untouched');
  assert.deepEqual(env.errors, []);
});

test('the palette opens from the button or Ctrl+K and lists skills safely', async () => {
  const dangerous = '<img src=x onerror=alert(1)> steal config';
  const env = await boot({
    fixtures: {
      '/api/skills': {
        skills: [{ name: 'review', slash_command: 'review', description: dangerous }],
      },
    },
  });

  env.document.getElementById('command-palette-btn').click();
  await gap();
  let palette = env.document.querySelector('.command-palette');
  assert.ok(palette, 'the footer button opened the palette');
  assert.ok(env.requests.includes('/api/skills'), 'slash-enabled skills were loaded');

  const rows = palette.querySelectorAll('.command-palette-item');
  assert.equal(rows.length, 6, 'five built-ins plus one skill');
  assert.ok(Array.from(rows).some((r) => r.textContent.startsWith('/review')));
  assert.equal(rows[rows.length - 1].querySelector('img'), null, 'skill text is not markup');
  assert.ok(rows[rows.length - 1].textContent.includes(dangerous), 'the unsafe description stays literal');

  palette.querySelector('#command-palette-cancel').click();
  assert.equal(env.document.querySelector('.command-palette'), null);

  env.window.dispatchEvent(new env.window.KeyboardEvent('keydown', {
    key: 'K', ctrlKey: true, bubbles: true, cancelable: true,
  }));
  await gap();
  palette = env.document.querySelector('.command-palette');
  assert.ok(palette, 'Ctrl+K reopened the palette');
  assert.deepEqual(env.errors, []);
});

test('choosing a skill pre-fills the input without sending a turn', async () => {
  const env = await boot({
    fixtures: {
      '/api/skills': {
        skills: [{ name: 'review', slash_command: 'review', description: 'Review code' }],
      },
    },
  });

  env.document.getElementById('command-palette-btn').click();
  await gap();
  const filter = env.document.getElementById('command-palette-input');
  filter.value = 'review';
  filter.dispatchEvent(new env.window.Event('input', { bubbles: true }));

  const rows = env.document.querySelectorAll('.command-palette-item');
  assert.equal(rows.length, 1, 'the list was filtered');
  key(filter, 'Enter');

  assert.equal(env.document.querySelector('.command-palette'), null, 'the palette closed');
  assert.equal(env.document.getElementById('user-input').value, '/review ', 'arguments can follow');
  assert.deepEqual(userMessages(env), [], 'the user still controls when it is sent');
  assert.deepEqual(env.errors, []);
});

test('choosing a built-in from the palette runs it immediately', async () => {
  const env = await boot();
  env.window.dispatchEvent(new env.window.KeyboardEvent('keydown', {
    key: 'k', metaKey: true, bubbles: true, cancelable: true,
  }));
  await gap();
  const filter = env.document.getElementById('command-palette-input');
  filter.value = 'undo';
  filter.dispatchEvent(new env.window.Event('input', { bubbles: true }));
  key(filter, 'Enter');

  assert.ok(env.socket.sent.some((m) => m.type === 'undo'), 'the built-in ran');
  assert.deepEqual(userMessages(env), []);
  assert.equal(env.document.querySelector('.command-palette'), null);
  assert.deepEqual(env.errors, []);
});
