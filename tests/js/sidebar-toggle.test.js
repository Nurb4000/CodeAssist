'use strict';
// Regression coverage for sidebar-collapse persistence (review item §1.12).
//
// The localStorage key is 'codeassist:sidebarVisible' ('true' = visible,
// 'false' = collapsed). initSidebarToggle() reads stored === 'false' to decide
// the collapsed state on load. The collapse and Ctrl+B handlers computed the new
// `hidden` state and wrote String(hidden) — so collapsing wrote "true" (which
// means *visible*), which reload-inverted back to a visible sidebar. The
// persisted collapsed state never survived a reload. The fix writes
// String(!hidden) so collapse stores "false".

const test = require('node:test');
const assert = require('node:assert');
const { boot, bootAdmin } = require('./harness.js');

const VISIBLE_KEY = 'codeassist:sidebarVisible';

function collapsed(env) {
  return env.document.getElementById('app').classList.contains('sidebar-hidden');
}
function stored(env) {
  return env.window.localStorage.getItem(VISIBLE_KEY);
}
function clickCollapse(env) {
  env.document.getElementById('sidebar-collapse').dispatchEvent(
    new env.window.MouseEvent('click', { bubbles: true, cancelable: true }),
  );
}
function clickReveal(env) {
  env.document.getElementById('sidebar-reveal').dispatchEvent(
    new env.window.MouseEvent('click', { bubbles: true, cancelable: true }),
  );
}
function pressCtrlB(env) {
  env.window.dispatchEvent(new env.window.KeyboardEvent('keydown', {
    key: 'b', ctrlKey: true, bubbles: true, cancelable: true,
  }));
}

// The main app page and the admin page share identical sidebar-toggle code, so
// run the same behavioural checks against both. `booter` is the harness boot
// function (boot or bootAdmin) so a test can seed localStorage via preload.
function checkPage(label, booter) {
  test(`${label}: collapse hides the sidebar and stores "false"`, async () => {
    const env = await booter();
    assert.strictEqual(stored(env), null, 'no stored value at first load');
    assert.strictEqual(collapsed(env), false, 'sidebar starts visible');

    clickCollapse(env);

    assert.strictEqual(collapsed(env), true, 'sidebar is collapsed after the click');
    assert.strictEqual(
      stored(env),
      'false',
      'collapse must persist the sidebar as not-visible (the bug wrote "true")',
    );
  });

  test(`${label}: Ctrl+B collapses and stores "false"`, async () => {
    const env = await booter();
    pressCtrlB(env);
    assert.strictEqual(collapsed(env), true, 'sidebar is collapsed after Ctrl+B');
    assert.strictEqual(stored(env), 'false', 'Ctrl+B persists the sidebar as not-visible');
  });

  test(`${label}: reveal shows the sidebar and stores "true"`, async () => {
    const env = await booter({
      preload: (w) => { w.localStorage.setItem(VISIBLE_KEY, 'false'); },
    });
    assert.strictEqual(collapsed(env), true, 'starts collapsed from stored "false"');

    clickReveal(env);
    assert.strictEqual(collapsed(env), false, 'sidebar shown after reveal');
    assert.strictEqual(stored(env), 'true', 'reveal persists the sidebar as visible');
  });

  test(`${label}: a persisted collapsed state reloads collapsed`, async () => {
    const env = await booter({
      preload: (w) => { w.localStorage.setItem(VISIBLE_KEY, 'false'); },
    });
    assert.strictEqual(collapsed(env), true, 'stored "false" reloads as collapsed');
  });

  test(`${label}: a persisted visible state reloads visible`, async () => {
    const env = await booter({
      preload: (w) => { w.localStorage.setItem(VISIBLE_KEY, 'true'); },
    });
    assert.strictEqual(collapsed(env), false, 'stored "true" reloads as visible');
  });
}

checkPage('[app.js]', boot);
checkPage('[admin.js]', bootAdmin);
