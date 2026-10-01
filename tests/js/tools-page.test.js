'use strict';
// Behavioural tests for the tool-manager page.
//
// Regression coverage for: the tool lists were rendered in whatever order the
// registry returned them, so "All Tools" and "Custom Tools" were not
// alphabetised. The admin page sorts by name; this page had no equivalent.

const test = require('node:test');
const assert = require('node:assert');
const { bootTools } = require('./harness.js');

// Deliberately not in alphabetical order, and with the two custom tools also out
// of order among themselves, so each assertion fails without the sort rather
// than passing on the registry's incidental ordering.
const TOOLS = {
  tools: [
    { name: 'zebra', type: 'builtin', description: 'z' },
    { name: 'beta', type: 'custom', description: 'b' },
    { name: 'mid', type: 'builtin', description: 'm' },
    { name: 'Alpha', type: 'custom', description: 'a' },
  ],
};

const cardNames = (doc, containerId) =>
  Array.from(doc.querySelectorAll(`#${containerId} .tool-card .tool-name`))
    .map((n) => n.textContent.trim().split(/\s/)[0]);

test('tools.js runs without error', async () => {
  const { errors } = await bootTools({ fixtures: { '/api/tools/manage/list': TOOLS } });
  assert.deepStrictEqual(errors, []);
});

test('the all-tools list is alphabetised', async () => {
  const { document: doc } = await bootTools({ fixtures: { '/api/tools/manage/list': TOOLS } });

  assert.deepStrictEqual(cardNames(doc, 'tools-list'), ['Alpha', 'beta', 'mid', 'zebra']);
});

test('the custom-tools list is alphabetised', async () => {
  const { window, document: doc } = await bootTools({ fixtures: { '/api/tools/manage/list': TOOLS } });

  const nav = doc.querySelector('.nav-item[data-page="custom"]');
  nav.dispatchEvent(new window.MouseEvent('click', { bubbles: true, cancelable: true }));
  // navigateTo kicks off loadPage without awaiting it, so let the fetch land.
  await new Promise((r) => setTimeout(r, 30));

  assert.deepStrictEqual(cardNames(doc, 'custom-tools-list'), ['Alpha', 'beta']);
});
