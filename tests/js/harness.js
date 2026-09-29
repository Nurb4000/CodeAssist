'use strict';
// Test harness for CodeAssist's frontend.
//
// The frontend is served unbundled, so the most faithful test is to boot the real
// index.html and the real app.js inside jsdom and drive them the way the browser
// would. Nothing here re-implements app.js logic; the only substitutions are the
// two things jsdom cannot provide: the network (fetch) and the WebSocket.
//
// app.js is a classic script, not a module. It is therefore evaluated inside the
// jsdom window's own context so its top-level `function` declarations (the event
// handlers we call) resolve against the real document. Top-level `let`/`const`
// stay lexical, which is fine: the DOM is the observable state we assert on.

const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { JSDOM } = require('jsdom');

const STATIC_DIR = path.join(__dirname, '..', '..', 'codeassist', 'static');

function readStatic(...parts) {
  return fs.readFileSync(path.join(STATIC_DIR, ...parts), 'utf8');
}

// Minimal responses for the REST calls the boot sequence makes.
const API_FIXTURES = {
  '/api/config': {
    llm: { model: 'test-model' },
    agent: { default_agent: 'default', agents: [{ id: 'default', name: 'CodeAssist' }] },
    server: { workspace: '/tmp/ws' },
    features: {},
  },
  '/api/sessions': [{ id: 'session-1', name: 'test' }],
  '/api/sessions/session-1/messages': [],
  '/api/todos': [],
  '/api/kb/stats': { total: 0 },
  '/api/agents': [{ id: 'default', name: 'CodeAssist' }],
};

class FakeWebSocket {
  static instances = [];
  static OPEN = 1;

  constructor(url) {
    this.url = url;
    this.readyState = 1; // OPEN
    this.sent = [];
    FakeWebSocket.instances.push(this);
  }

  send(data) {
    this.sent.push(JSON.parse(data));
  }

  close() {
    this.readyState = 3;
  }

  /** Push a server event into the app's message dispatcher. */
  receive(payload) {
    this.onmessage({ data: JSON.stringify(payload) });
  }
}

/**
 * Boot the real frontend in jsdom.
 *
 * @param {object} [options]
 * @param {object} [options.fixtures] Extra/overriding REST responses, keyed by
 *   path (query string stripped) or by exact URL including the query string,
 *   the latter taking precedence. Merged over API_FIXTURES, so a test can serve
 *   a session whose messages actually carry `reasoning_content`, or give two
 *   sessions different plans. A value may be a function of the HTTP method for
 *   endpoints hit with several verbs.
 * @returns {{
 *   window: Window, document: Document, sockets: FakeWebSocket[],
 *   socket: FakeWebSocket, feed: (payload: object) => void,
 *   ready: Promise<void>, errors: string[]
 * }}
 */
async function boot({ fixtures = {} } = {}) {
  const errors = [];
  const requests = [];
  const routes = { ...API_FIXTURES, ...fixtures };
  const dom = new JSDOM(readStatic('index.html'), {
    url: 'http://localhost:8000/',
    pretendToBeVisual: true,
    runScripts: 'outside-only',
  });
  const { window } = dom;

  // The vendor bundles app.js expects to find as globals.
  const context = dom.getInternalVMContext();
  vm.runInContext(readStatic('vendor', 'marked.min.js'), context, { filename: 'marked.min.js' });
  vm.runInContext(readStatic('vendor', 'highlight.min.js'), context, { filename: 'highlight.min.js' });

  window.addEventListener('error', (e) => errors.push(String(e.error || e.message)));

  window.fetch = async (url, opts = {}) => {
    const full = String(url);
    requests.push(full);
    const pathOnly = full.split('?')[0];
    // Prefer an exact-URL fixture (query string included) so a test can give two
    // sessions different responses for the same endpoint, e.g. /api/todos.
    const entry = routes[full] !== undefined ? routes[full] : routes[pathOnly];
    // A fixture may be a function of the HTTP method, for endpoints the app
    // hits with more than one verb (/api/sessions is both a list and a create).
    const body = typeof entry === 'function'
      ? entry((opts && opts.method) || 'GET')
      : (entry !== undefined ? entry : {});
    return {
      ok: true,
      status: 200,
      json: async () => body,
      text: async () => JSON.stringify(body),
    };
  };
  window.WebSocket = FakeWebSocket;

  vm.runInContext(readStatic('app.js'), context, { filename: 'app.js' });

  // app.js boots via an async IIFE (loadConfig -> sessions -> connectWS). Let it
  // settle so a socket exists before the test drives events.
  await new Promise((r) => setTimeout(r, 30));

  const socket = FakeWebSocket.instances[FakeWebSocket.instances.length - 1];
  if (!socket) errors.push('no WebSocket was created by the boot sequence');

  return {
    window,
    document: window.document,
    sockets: FakeWebSocket.instances,
    socket,
    feed: (payload) => socket.receive(payload),
    ready: Promise.resolve(),
    errors,
    requests,
  };
}

/** Start a turn: the same entry point the Send button uses. */
function startTurn(env, text = 'hello') {
  env.window.document.getElementById('user-input').value = text;
  env.window.sendMessage();
}

/** The work block, once one exists. */
function workBlock(document) {
  return document.querySelector('.work-block');
}

function steps(document) {
  return Array.from(document.querySelectorAll('.work-step'));
}

/**
 * The tool outputs a step has actually received.
 *
 * Every tool call renders an output slot up front, so an un-resulted call shows
 * as an empty node. Those are filtered out: a test asking "was this result
 * delivered?" wants the delivered ones, not the pending slots.
 */
function stepOutputs(step) {
  return Array.from(step.querySelectorAll('.tool-call-output'))
    .map((n) => n.textContent.trim())
    .filter(Boolean);
}

module.exports = { boot, startTurn, workBlock, steps, stepOutputs, FakeWebSocket };
