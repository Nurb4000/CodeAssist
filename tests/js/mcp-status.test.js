'use strict';
// C3: the admin page must surface live MCP discovery state (tools, prompts,
// resources, instructions) and connection errors, not just the saved config.

const test = require('node:test');
const assert = require('node:assert');
const { bootAdmin } = require('./harness.js');

const settle = () => new Promise((r) => setTimeout(r, 30));

test('MCP status panel shows discovery details and connection errors', async () => {
  const env = await bootAdmin({
    fixtures: {
      '/api/mcp/status': {
        enabled: true,
        servers: [{
          name: 'remote',
          transport: 'sse',
          connected: false,
          error: "OAuth provider 'github' has no access token",
          tools: ['mcp_remote_echo'],
          prompts: [{ name: 'summarize', description: 'Summarize a doc' }],
          resources: [{ uri: 'file://docs/readme.md', name: 'readme' }],
          instructions: 'Be terse',
          oauth_provider: 'github',
        }],
      },
    },
  });

  assert.deepStrictEqual(env.errors, []);
  const text = env.document.getElementById('mcp-status').textContent;
  for (const expected of [
    'remote', 'sse', 'disconnected', 'mcp_remote_echo', 'summarize',
    'file://docs/readme.md', 'Be terse', 'OAuth: github',
    "OAuth provider 'github' has no access token",
  ]) {
    assert.ok(text.includes(expected), `status panel should show ${expected}: ${text}`);
  }
});

test('MCP status panel explains disabled MCP', async () => {
  const { document: doc, errors } = await bootAdmin();
  assert.deepStrictEqual(errors, []);
  assert.match(doc.getElementById('mcp-status').textContent, /MCP is disabled/);
});

test('reloading MCP connections refreshes the status panel', async () => {
  const env = await bootAdmin();
  const calls = [];
  const realFetch = env.window.fetch;
  env.window.fetch = async (url, opts = {}) => {
    calls.push(`${opts.method || 'GET'} ${String(url).split('?')[0]}`);
    return realFetch(url, opts);
  };

  env.document.getElementById('mcp-reload').dispatchEvent(
    new env.window.MouseEvent('click', { bubbles: true, cancelable: true }),
  );
  await settle();

  assert.ok(calls.includes('POST /api/mcp/reload'), 'reload endpoint is called');
  assert.ok(calls.includes('GET /api/mcp/status'), 'status is refreshed after reconnect');
});
