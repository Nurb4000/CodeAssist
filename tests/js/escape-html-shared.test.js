'use strict';
// Regression for the §11 escapeHtml dedup. The four page scripts each used to
// define their own escapeHtml; two were byte-identical regex variants and one
// (kb.js) was a DOM-based textContent -> innerHTML variant that left " and '
// untouched -- unsafe where values are interpolated into double-quoted
// attributes or single-quoted inline onclick="..." strings. All pages now load
// one canonical helper from /static/shared/escapeHtml.js, so the global is
// defined exactly once and always escapes quotes.

const test = require('node:test');
const assert = require('node:assert');
const { bootTools } = require('./harness.js');

test('the page scripts share one canonical escapeHtml global', async () => {
  const { window, errors } = await bootTools();
  assert.deepStrictEqual(errors, [], 'shared helper must load without error');
  assert.strictEqual(typeof window.escapeHtml, 'function', 'escapeHtml is the loaded global, not a per-file copy');
});

test('it escapes quotes as well as & < > so attribute/out-of-string injection cannot break out', async () => {
  const { window } = await bootTools();
  // The exact breakout the old DOM-based variant allowed: an apostrophe closing
  // a single-quoted onclick="..." string. The regex variant escapes it; the DOM
  // variant left it through, which is how tool names broke out of
  // onclick="viewTool('...')" here.
  assert.strictEqual(window.escapeHtml("a'b\"c<d>e&"), 'a&#39;b&quot;c&lt;d&gt;e&amp;');
  assert.strictEqual(window.escapeHtml("evil');alert(1);//"), "evil&#39;);alert(1);//");
});

test('it coerces non-strings and null/undefined like the shared implementation', async () => {
  const { window } = await bootTools();
  assert.strictEqual(window.escapeHtml(undefined), '');
  assert.strictEqual(window.escapeHtml(null), '');
  assert.strictEqual(window.escapeHtml(123), '123');
  assert.strictEqual(window.escapeHtml({}), '[object Object]');
});
