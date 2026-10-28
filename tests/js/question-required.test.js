'use strict';
// Regression for §9: a required question could be submitted with an empty
// answer. That empty answer was sent to the server, which then errored
// mid-turn ("No answer received.") instead of giving clear inline feedback.
// Submit now blocks an empty answer for required questions and shows an inline
// error; non-required questions and answered required questions are unaffected.

const test = require('node:test');
const assert = require('node:assert');
const { boot } = require('./harness.js');

const sentTypes = (env) =>
  env.sockets.flatMap((s) => (s.sent || []).map((m) => m.type));

const submit = (env) => env.document.querySelector('.question-dialog .submit');
const customInput = (env) => env.document.querySelector('.question-custom');
const dialogError = (env) => env.document.querySelector('.question-error');

test('an empty answer to a required question is blocked with inline feedback', async () => {
  const env = await boot();
  env.feed({ type: 'question_request', id: 'q1', question: 'Required?', required: true });

  submit(env).click();

  assert.ok(
    !sentTypes(env).includes('question_response'),
    'the empty required answer is not sent to the server',
  );
  const err = dialogError(env);
  assert.ok(err && /required/i.test(err.textContent), 'an inline error explains why');
});

test('answering a required question submits normally', async () => {
  const env = await boot();
  env.feed({ type: 'question_request', id: 'q1', question: 'Required?', required: true });

  customInput(env).value = 'My answer';
  submit(env).click();

  assert.ok(sentTypes(env).includes('question_response'), 'the answer is sent');
  assert.strictEqual(dialogError(env), null, 'no error once an answer is provided');
});

test('a non-required question accepts an empty answer (control)', async () => {
  const env = await boot();
  env.feed({ type: 'question_request', id: 'q1', question: 'Anything?', required: false });

  submit(env).click();

  assert.ok(sentTypes(env).includes('question_response'), 'empty answers are allowed when not required');
});
