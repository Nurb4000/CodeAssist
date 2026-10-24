'use strict';
// Regression coverage for §9: highlightToolOutput() double-escaped its input.
//
// It ran escapeHtml() and THEN passed the result through highlight.js, which
// escapes HTML entities again -- so a tool output containing '&' rendered as
// '&amp;amp;' (and quotes/angle brackets similarly mangled). highlight.js
// already returns entity-escaped HTML, so the pre-escape is redundant and
// wrong. Also the code-block template closed in the wrong order (</pre></code>).

const test = require('node:test');
const assert = require('node:assert');
const { boot } = require('./harness.js');

test('a JSON tool output with & is escaped once, not twice', async () => {
  const env = await boot();
  const out = env.window.highlightToolOutput('{"a": "x&y"}');
  assert.ok(out.includes('&amp;'), 'the ampersand should survive as a single &amp;');
  assert.ok(!out.includes('&amp;amp;'), 'double-escaping the ampersand is the bug');
});

test('multi-line output with < and & is escaped once', async () => {
  const env = await boot();
  const out = env.window.highlightToolOutput('first\na < b & c > d');
  assert.ok(!out.includes('&amp;amp;'), 'no double-escaping of &');
  assert.ok(!out.includes('< b'), 'raw < must be escaped');
  assert.ok(!out.includes('> d'), 'raw > must be escaped');
});

test('a code block closes </code></pre> in the right order', async () => {
  const env = await boot();
  const out = env.window.highlightToolOutput('line1\nline2');
  assert.ok(out.includes('</code></pre>'), 'must close </code></pre>');
  assert.ok(!out.includes('</pre></code>'), 'mismatched tag order is the bug');
});
