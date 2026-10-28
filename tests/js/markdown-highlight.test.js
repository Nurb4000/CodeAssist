'use strict';
// Regression for §9: marked v12 dropped client-side syntax highlighting (its
// `highlight` option is ignored), so fenced code in assistant prose came through
// as plain <code> while tool outputs were highlighted via highlightToolOutput()
// -- an inconsistent look plus dead config at the top of app.js. renderMarkdown()
// now post-processes marked's HTML to run highlight.js over each <pre><code>
// block, then sanitizeHtml still strips anything dangerous as a backstop.

const test = require('node:test');
const assert = require('node:assert');
const { boot } = require('./harness.js');

test('fenced code in prose is syntax-highlighted through renderMarkdown', async () => {
  const env = await boot();
  const out = env.window.renderMarkdown('```js\nconst x = 1;\n```');
  assert.ok(/hljs-/.test(out), 'highlight.js wrapped the code in highlighted spans');
  assert.ok(/<pre><code/.test(out), 'the code stays wrapped in pre>code');
});

test('non-code prose is unaffected', async () => {
  const env = await boot();
  const out = env.window.renderMarkdown('Hello **bold** and a [link](https://example.com).');
  assert.ok(out.includes('<strong>bold</strong>'), 'emphasis still renders');
  assert.ok(out.includes('href="https://example.com"'), 'a safe link is kept');
});

test('XSS vectors are still stripped after highlighting', async () => {
  const env = await boot();
  const out = env.window.renderMarkdown('<script>alert(1)</script>img <img src=x onerror=alert(1)>');
  assert.ok(!/<script/i.test(out), 'script tags are removed');
  assert.ok(!/onerror/i.test(out), 'inline event handlers are stripped');
});
