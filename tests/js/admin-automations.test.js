'use strict';
// Admin automation rows expose their append-only run history on demand. The
// status column alone cannot explain why a job failed, so the Runs action must
// fetch /runs and render output/errors as text (never interpolated HTML).

const test = require('node:test');
const assert = require('node:assert');
const { bootAdmin } = require('./harness.js');

test('Runs reveals automation history with escaped output', async () => {
  const env = await bootAdmin({
    fixtures: {
      '/api/automations': {
        enabled: true,
        automations: [{
          id: 'job-1', name: 'nightly', kind: 'cron', schedule: '0 3 * * *',
          action: 'prompt', next_run_at: '2026-11-28T03:00:00+00:00', last_status: 'error',
        }],
      },
      '/api/automations/job-1/runs': {
        runs: [{
          started_at: '2026-11-27T03:00:00+00:00', status: 'error',
          output: 'partial <b>text</b>', error: 'boom <script>alert(1)</script>',
        }],
      },
    },
  });
  const doc = env.document;
  const row = doc.querySelector('#automation-body tr');
  assert.ok(row, 'automation row rendered');
  const runsBtn = Array.from(row.querySelectorAll('button'))
    .find((b) => b.textContent === 'Runs');
  assert.ok(runsBtn, 'Runs action present');

  runsBtn.dispatchEvent(new env.window.MouseEvent('click', { bubbles: true }));
  await new Promise((resolve) => setTimeout(resolve, 30));

  const detail = doc.querySelector('.automation-runs-row');
  assert.ok(detail, 'history row inserted');
  const text = detail.textContent;
  assert.match(text, /2026-11-27T03:00:00/);
  assert.match(text, /boom <script>alert\(1\)<\/script>/);
  assert.match(text, /partial <b>text<\/b>/);
  assert.strictEqual(detail.querySelector('script'), null, 'run output is escaped');
  assert.strictEqual(detail.querySelector('b'), null, 'run output is escaped');

  runsBtn.dispatchEvent(new env.window.MouseEvent('click', { bubbles: true }));
  assert.strictEqual(doc.querySelector('.automation-runs-row'), null, 'Runs toggles closed');
  assert.deepStrictEqual(env.errors, []);
});
