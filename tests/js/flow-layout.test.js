'use strict';
// Layout contract for the message flow.
//
// Every element app.js inserts directly into #messages renders in the same 800px
// content column as .message (max-width + auto side margins). #messages is a plain
// block container, so an inserted child stretches to the full scroll width unless
// it opts into the column -- which made the Work block, the active step and the
// Past thinking container span edge to edge while the prompt and response around
// them were inset, reading as a different, wider layer.
//
// jsdom does not fetch the <link rel=stylesheet> in index.html, so
// getComputedStyle() would report empty here. The rules are asserted against the
// stylesheet source instead, which is what actually ships.

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { boot, startTurn } = require('./harness.js');

const CSS_PATH = path.join(__dirname, '..', '..', 'codeassist', 'static', 'style.css');
const css = fs.readFileSync(CSS_PATH, 'utf8');

/** The declaration block for a top-level selector, or '' if it has no rule. */
function ruleFor(selector) {
  // Flat one-level selectors only (`.foo`, `#bar`, `div > .foo > .bar`); good
  // enough for the flow sections and it will not match `.foo .bar` by accident.
  const flat = new RegExp(`^\\s*${selector.replace(/[.*+?^${}()|[\\]\\\\]/g, '\\\\$&')}\\s*\\{([^}]*)\\}`, 'm');
  const m = css.match(flat);
  return m ? m[1] : '';
}

/**
 * A flow section belongs to the shared column: it is capped at the message width
 * and centred with auto side margins.
 */
function assertInMessageColumn(selector, where) {
  const body = ruleFor(selector);
  assert.ok(body, `${selector} must have a rule in style.css (missing ${where})`);
  assert.match(
    body, /max-width:\s*800px/,
    `${selector} must declare max-width: 800px to share the .message column (${where})`
  );
  const margin = (body.match(/margin:([^;]*)/) || [])[1] || '';
  assert.match(
    margin.trim(), /(^|\s)auto(\s|$)/,
    `${selector} needs auto side margins to centre in the column, got margin: ${margin.trim()} (${where})`
  );
}

// The selectors app.js inserts as direct children of #messages. Keep in sync with
// what actually lands there -- the test below fails on any unlisted class.
const FLOW_SECTIONS = [
  ['.message', 'every prompt and response'],
  ['.work-block', 'the collapsible Work section'],
  ['.work-active', 'the live step, a sibling of the work block'],
  ['.thinking-history', 'the Past thinking container'],
  ['.progress-bar', 'the run progress bar'],
  ['.message-actions', 'the copy/retry row under a response'],
];

for (const [selector, where] of FLOW_SECTIONS) {
  test(`${selector} shares the .message column (${where})`, () => {
    assertInMessageColumn(selector, where);
  });
}

test('every element inserted into #messages is accounted for', async () => {
  // Drive a reloaded prose-only turn plus a live tool-using turn, so the flow
  // holds a prompt, a response, the work block, the active step and the Past
  // thinking container at once -- one full set of flow sections.
  const env = await boot({
    fixtures: {
      '/api/sessions': [{ id: 'session-1', name: 'one' }],
      '/api/sessions/session-1/messages': [
        { id: 'u1', role: 'user', content: 'answer without tools' },
        { id: 'a1', role: 'assistant', content: 'The answer.', reasoning_content: 'Quietly thought.' },
      ],
    },
  });
  startTurn(env, 'tool turn');
  env.feed({ type: 'reasoning', content: 'Plan the change.' });
  env.feed({ type: 'tool_call', name: 'read', arguments: { file_path: 'a.py' }, id: 't1' });
  env.feed({ type: 'tool_result', id: 't1', output: 'ok' });
  env.feed({ type: 'text_delta', content: 'Done.' });
  env.feed({ type: 'done' });

  const known = new Set(FLOW_SECTIONS.map(([sel]) => sel.slice(1)));
  const unlisted = Array.from(env.document.querySelector('#messages').children)
    .map((el) => el.className.trim().split(/\s+/)[0])
    .filter((c) => c && !known.has(c));

  assert.deepStrictEqual(
    Array.from(new Set(unlisted)), [],
    'these #messages children are not in FLOW_SECTIONS; give them the column too or say why not'
  );
});

test('the work step nests inside the column rather than widening it', async () => {
  // The step is a descendant, so it must NOT carry its own max-width: capping it
  // again would make it narrower than the section wrapping it.
  const env = await boot();
  startTurn(env, 'do the thing');
  env.feed({ type: 'reasoning', content: 'Plan the change.' });
  env.feed({ type: 'tool_call', name: 'read', arguments: { file_path: 'a.py' }, id: 't1' });
  env.feed({ type: 'tool_result', id: 't1', output: 'ok' });

  const step = env.document.querySelector('.work-active .work-step');
  assert.ok(step, 'the active step is rendered');
  const body = ruleFor('.work-step');
  assert.ok(!/max-width/.test(body), 'a descendant step must not re-cap the width');
});
