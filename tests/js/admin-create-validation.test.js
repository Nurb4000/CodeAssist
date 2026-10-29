'use strict';
// Regression for a §10 GAP: the generic admin create handlers (bindCreate, used
// by the skill/mcp/lsp forms) submitted whatever buildBody() produced with no
// client-side required-field check, so an empty Name/Command round-tripped to
// the server and failed there instead of being rejected instantly.
// agent-create already validated its field; this brings the generic forms in
// line. A valid submit must still POST.

const test = require('node:test');
const assert = require('node:assert');
const { bootAdmin } = require('./harness.js');

const clickCreate = (env, id) =>
  env.document.getElementById(id).dispatchEvent(
    new env.window.MouseEvent('click', { bubbles: true, cancelable: true }),
  );

const settle = () => new Promise((r) => setTimeout(r, 20));

test('an empty MCP name is rejected client-side without hitting the server', async () => {
  const calls = [];
  const env = await bootAdmin();
  env.window.fetch = async (url, opts) => {
    calls.push(`${opts.method} ${String(url).split('?')[0]}`);
    return { ok: true, status: 200, json: async () => ({}) };
  };

  // mcp-name is left empty.
  clickCreate(env, 'mcp-create');
  await settle();

  const msg = env.document.getElementById('admin-status').textContent;
  assert.match(msg, /required/i, 'the status shows a required-field error');
  assert.ok(
    !calls.some((c) => c.includes('/api/mcp/servers')),
    'no POST is made for an invalid submit',
  );
});

test('an empty LSP command is rejected client-side', async () => {
  const calls = [];
  const env = await bootAdmin();
  env.window.fetch = async (url) => {
    calls.push(String(url).split('?')[0]);
    return { ok: true, status: 200, json: async () => ({}) };
  };

  // Name filled, command empty.
  env.document.getElementById('lsp-name').value = 'my-lsp';
  env.document.getElementById('lsp-command').value = '';
  clickCreate(env, 'lsp-create');
  await settle();

  const msg = env.document.getElementById('admin-status').textContent;
  assert.match(msg, /required/i);
  assert.ok(!calls.some((c) => c.includes('/api/lsp/servers')), 'no POST is made for an invalid submit');
});

test('a valid MCP create still posts to the server', async () => {
  const calls = [];
  const env = await bootAdmin();
  env.window.fetch = async (url, opts) => {
    calls.push(`${opts.method} ${String(url).split('?')[0]}`);
    return { ok: true, status: 200, json: async () => ({}) };
  };

  env.document.getElementById('mcp-name').value = 'good-name';
  env.document.getElementById('mcp-config').value = '{"command":"echo"}';
  clickCreate(env, 'mcp-create');
  await settle();

  assert.ok(calls.some((c) => c === 'POST /api/mcp/servers'), 'a valid submit is posted');
  assert.doesNotMatch(
    env.document.getElementById('admin-status').textContent,
    /required/i,
    'no error shown on success',
  );
});
