'use strict';
// Regression for §9 a11y GAP: the question and confirm dialogs were plain divs
// with no dialog semantics, so assistive tech did not announce them as modals or
// know their titles. They now expose role="dialog" + aria-modal="true", each
// labelled by its title via aria-labelledby (§9 a11y).

const test = require('node:test');
const assert = require('node:assert');
const { boot } = require('./harness.js');

test('a question dialog exposes role=dialog, aria-modal, and a labelled title', async () => {
  const env = await boot();
  env.feed({ type: 'question_request', id: 'q1', question: 'Which one?', required: false });

  const dialog = env.document.querySelector('.question-dialog');
  assert.ok(dialog, 'the question dialog is rendered');
  assert.strictEqual(dialog.getAttribute('role'), 'dialog', 'announced as a dialog');
  assert.strictEqual(dialog.getAttribute('aria-modal'), 'true', 'a modal dialog');
  const labelledBy = dialog.getAttribute('aria-labelledby');
  assert.ok(labelledBy, 'the dialog references its title');
  const title = env.document.getElementById(labelledBy);
  assert.ok(title && title.classList.contains('question-title'), 'the labelled element is the title');
});

test('a confirm dialog exposes role=dialog, aria-modal, and a labelled title', async () => {
  const env = await boot();
  env.feed({
    type: 'confirm_request',
    id: 'c1',
    tool: 'shell',
    arguments: { command: 'ls' },
    in_workspace: true,
  });

  const dialog = env.document.querySelector('.confirm-dialog');
  assert.ok(dialog, 'the confirm dialog is rendered');
  assert.strictEqual(dialog.getAttribute('role'), 'dialog', 'announced as a dialog');
  assert.strictEqual(dialog.getAttribute('aria-modal'), 'true', 'a modal dialog');
  const labelledBy = dialog.getAttribute('aria-labelledby');
  assert.ok(labelledBy, 'the dialog references its title');
  const title = env.document.getElementById(labelledBy);
  assert.ok(title && title.classList.contains('confirm-title'), 'the labelled element is the title');
});
