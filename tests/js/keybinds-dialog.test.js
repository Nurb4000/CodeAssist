'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { boot } = require('./harness.js');

function openKeybinds(env) {
  env.document.getElementById('keybinds-btn').click();
  return env.document.querySelector('.keybinds-modal');
}

test('keybinds dialog has dialog semantics and focus trap', async () => {
  const env = await boot();
  const modal = openKeybinds(env);
  assert.ok(modal, 'modal opens');
  assert.strictEqual(modal.getAttribute('role'), 'dialog');
  assert.strictEqual(modal.getAttribute('aria-modal'), 'true');
  assert.strictEqual(modal.getAttribute('aria-labelledby'), 'keybinds-title');

  const inputs = Array.from(modal.querySelectorAll('.keybind-input'));
  const buttons = Array.from(modal.querySelectorAll('button'));
  const focusables = [...inputs, ...buttons];
  assert.ok(focusables.length >= 4);

  // initial focus should land on first input
  assert.strictEqual(env.document.activeElement, inputs[0]);

  // Tab wrap forward
  focusables[focusables.length - 1].focus();
  const tab = new env.window.KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true });
  modal.dispatchEvent(tab);
  assert.ok(tab.defaultPrevented);
  assert.strictEqual(env.document.activeElement, focusables[0]);

  // Shift+Tab wrap backward
  focusables[0].focus();
  const shiftTab = new env.window.KeyboardEvent('keydown', { key: 'Tab', shiftKey: true, bubbles: true, cancelable: true });
  modal.dispatchEvent(shiftTab);
  assert.ok(shiftTab.defaultPrevented);
  assert.strictEqual(env.document.activeElement, focusables[focusables.length - 1]);
});

test('keybinds dialog detects conflicts and blocks save', async () => {
  const env = await boot();
  const modal = openKeybinds(env);
  const inputs = Array.from(modal.querySelectorAll('.keybind-input'));
  inputs[0].value = 'Mod+K';
  inputs[1].value = 'Mod+K';
  inputs[1].dispatchEvent(new env.window.Event('input', { bubbles: true }));

  const conflict = modal.querySelector('#keybinds-conflict');
  assert.match(conflict.textContent, /Conflicts:/);
  assert.ok(inputs[0].classList.contains('keybind-conflict'));
  assert.ok(inputs[1].classList.contains('keybind-conflict'));
  assert.strictEqual(inputs[0].getAttribute('aria-invalid'), 'true');

  modal.querySelector('#keybinds-save').click();
  assert.ok(env.document.querySelector('.keybinds-modal'), 'modal remains open on conflict');
});

test('keybinds dialog saves normalized bindings and restores focus', async () => {
  const env = await boot();
  const opener = env.document.getElementById('keybinds-btn');
  opener.focus();
  const modal = openKeybinds(env);
  const input = modal.querySelector('.keybind-input[data-kb="commandPalette"]');
  input.value = 'ctrl+shift+p';
  input.dispatchEvent(new env.window.Event('input', { bubbles: true }));
  modal.querySelector('#keybinds-save').click();

  assert.ok(!env.document.querySelector('.keybinds-modal'), 'modal closes after save');
  const stored = JSON.parse(env.window.localStorage.getItem('codeassist:keybinds'));
  assert.strictEqual(stored.commandPalette, 'Mod+Shift+P');
  assert.strictEqual(env.document.activeElement, opener, 'focus restored to opener');
});

test('keybinds reset restores defaults', async () => {
  const env = await boot();
  env.window.localStorage.setItem('codeassist:keybinds', JSON.stringify({
    toggleSidebar: 'Mod+1', commandPalette: 'Mod+2', searchMessages: 'Mod+3', sendMessage: 'Enter'
  }));
  const modal = openKeybinds(env);
  modal.querySelector('#keybinds-reset').click();
  const vals = Array.from(modal.querySelectorAll('.keybind-input')).map(i => i.value);
  assert.deepStrictEqual(vals, ['Mod+B', 'Mod+K', 'Mod+Shift+F', 'Enter']);
  assert.match(modal.querySelector('#keybinds-conflict').textContent, /No conflicts/);
});
