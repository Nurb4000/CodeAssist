'use strict';
// Admin provider-profile controls and the searchable model picker. The picker is
// a native datalist driven by /api/config/test-connection; a probe must not
// overwrite a model the user already chose.

const test = require('node:test');
const assert = require('node:assert');
const { bootAdmin } = require('./harness.js');

const SETTINGS = {
  settings: [
    { key: 'llm.provider', label: 'Provider', group: 'LLM', type: 'str', value: 'custom', source: 'profile' },
    { key: 'llm.model', label: 'Model', group: 'LLM', type: 'str', value: 'llama3', source: 'profile' },
    { key: 'llm.base_url', label: 'Base URL', group: 'LLM', type: 'str', value: 'http://localhost:11434/v1', source: 'profile' },
    { key: 'llm.api_key', label: 'API Key', group: 'LLM', type: 'str', value: null, has_value: true, source: 'profile' },
    { key: 'llm.active_profile', label: 'Active provider profile', group: 'LLM', type: 'str', value: 'local', source: 'ui', hidden: true },
  ],
};

const PROFILES = {
  name: 'local', // also serves as the POST /api/provider-profiles response
  active: 'local',
  profiles: [{
    name: 'local', provider: 'custom', base_url: 'http://localhost:11434/v1',
    api_key: null, has_key: true, model: 'llama3', context_window: 32768, timeout: 120,
  }],
};

test('active profile is selected and its secret is never echoed into the form', async () => {
  const { document: doc, errors } = await bootAdmin({
    fixtures: { '/api/settings': SETTINGS, '/api/provider-profiles': PROFILES },
  });
  assert.deepStrictEqual(errors, []);
  assert.strictEqual(doc.getElementById('provider-profile-select').value, 'local');
  assert.strictEqual(doc.getElementById('profile-name').value, 'local');
  assert.strictEqual(doc.getElementById('profile-base-url').value, 'http://localhost:11434/v1');
  assert.strictEqual(doc.getElementById('profile-api-key').value, '', 'stored key stays masked');
  assert.strictEqual(doc.getElementById('set-llm.active_profile'), null, 'hidden setting has no duplicate row');
  assert.strictEqual(doc.getElementById('set-llm.model').getAttribute('list'), 'llm-model-options');
});

test('test-connection fills the model picker without replacing an existing choice', async () => {
  const { document: doc, window, errors, requests } = await bootAdmin({
    fixtures: {
      '/api/settings': SETTINGS,
      '/api/provider-profiles': PROFILES,
      '/api/config/test-connection': {
        ok: true, endpoint: 'http://localhost:11434/v1/models',
        models: ['llama3', 'llama3:70b', 'qwen2'], context_window: 32768,
      },
    },
  });
  doc.getElementById('settings-test').dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
  await new Promise((resolve) => setTimeout(resolve, 30));

  assert.deepStrictEqual(errors, []);
  const probe = requests.find((r) => r.url === '/api/config/test-connection');
  assert.ok(probe, 'probe was sent');
  assert.strictEqual(probe.method, 'POST');
  assert.strictEqual(probe.body.profile, 'local');
  assert.strictEqual(probe.body.api_key, undefined, 'masked key is not sent back');

  const options = Array.from(doc.querySelectorAll('#llm-model-options option')).map((o) => o.value);
  assert.deepStrictEqual(options, ['llama3', 'llama3:70b', 'qwen2']);
  assert.strictEqual(doc.getElementById('set-llm.model').value, 'llama3', 'existing choice preserved');
});

test('profile save and activation use the dedicated endpoints', async () => {
  const { document: doc, window, errors, requests } = await bootAdmin({
    fixtures: { '/api/settings': SETTINGS, '/api/provider-profiles': PROFILES },
  });
  doc.getElementById('profile-model').value = 'llama3:70b';
  doc.getElementById('profile-save').dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
  await new Promise((resolve) => setTimeout(resolve, 30));

  const saved = requests.find((r) => r.method === 'POST' && r.url === '/api/provider-profiles');
  assert.ok(saved, 'profile save sent');
  assert.strictEqual(saved.body.name, 'local');
  assert.strictEqual(saved.body.model, 'llama3:70b');
  assert.strictEqual(saved.body.api_key, '', 'blank key means keep stored secret');

  doc.getElementById('provider-profile-select').value = '';
  doc.getElementById('provider-profile-activate').dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
  await new Promise((resolve) => setTimeout(resolve, 30));
  const activation = requests.find(
    (r) => r.method === 'PUT' && r.url === '/api/settings' && 'llm.active_profile' in r.body,
  );
  assert.ok(activation, 'activation sent through the settings API');
  assert.strictEqual(activation.body['llm.active_profile'], '');
  assert.deepStrictEqual(errors, []);
});
