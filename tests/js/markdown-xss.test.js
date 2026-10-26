'use strict';
// Regression coverage for the marked.parse XSS gap (§9 / §13#5).
//
// marked v12 has no built-in sanitizer: its parse() passes raw HTML through
// unchanged, so model- or prompt-injected prose (<script>, inline event
// handlers such as <img onerror=..., and javascript: URLs) would be injected
// live into the DOM via innerHTML in a real browser. sanitizeHtml() strips
// those before any marked output is rendered.
//
// The guard exercises sanitizeHtml directly on marked's raw output: jsdom's
// innerHTML parser neutralizes <script>/onerror itself, so a structural DOM
// assertion after an innerHTML write could not distinguish fixed from unfixed —
// the sanitizer's string-in / string-out is what must be tested.

const test = require('node:test');
const assert = require('node:assert');
const { boot, startTurn } = require('./harness.js');

test('sanitizeHtml strips script, inline handlers, and javascript: URLs from marked output', async () => {
  const env = await boot();
  const marked = env.window.marked;
  const sanitize = env.window.sanitizeHtml;
  assert.strictEqual(typeof sanitize, 'function', 'sanitizeHtml is wired into app.js');

  const raw = marked.parse(
    '<p>Hi <img src=x onerror=alert(1)> and <script>alert(1)</script> ' +
    'and <a href="javascript:alert(1)">l</a></p>',
  );
  // The vector really is present before sanitization (guards against a marked
  // version that escapes by default).
  assert.ok(/<script/i.test(raw) && /onerror/i.test(raw) && /javascript:/i.test(raw),
    'marked output carries the injected vectors');

  const clean = sanitize(raw);
  assert.ok(!/<script/i.test(clean), 'no <script> survives sanitization');
  assert.ok(!/onerror/i.test(clean), 'inline event handler is stripped');
  assert.ok(!/javascript:/i.test(clean) && !/data:/i.test(clean), 'no javascript:/data: URL survives');
  assert.ok(clean.includes('<img'), 'the (de-hazarded) img element is kept');
});

test('sanitizeHtml preserves legitimate markdown rendering', async () => {
  const env = await boot();
  const sanitize = env.window.sanitizeHtml;

  const src = '<p>Hello <strong>bold</strong>, a <code>code</code> span, ' +
    'and <a href="https://example.com">a link</a>.</p>';
  const clean = sanitize(src);
  assert.ok(clean.includes('<strong>bold</strong>'), 'emphasis is preserved');
  assert.ok(clean.includes('<code>'), 'inline code is preserved');
  assert.match(clean, /href="https:\/\/example\.com"/, 'a safe https link is preserved');
  assert.ok(!/javascript:/i.test(clean));
});

test('streamed prose still renders legit content end-to-end', async () => {
  const env = await boot();
  startTurn(env);
  env.feed({ type: 'text_delta', content: 'Answer **done**.' });
  env.feed({ type: 'done' });

  const text = Array.from(env.document.querySelectorAll('#messages .message-content'))
    .map((c) => c.textContent).join(' ');
  assert.ok(text.includes('Answer'), 'prose text is rendered');
  assert.ok(
    env.document.querySelectorAll('#messages .message-content strong').length >= 1,
    'emphasis still renders through the streaming path',
  );
});
