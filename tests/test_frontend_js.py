"""Run the frontend test suite as part of the Python test run.

The static frontend is served unbundled, so its behaviour is covered by jsdom
tests under ``tests/js/`` driven by Node's built-in test runner. This module
keeps ``pytest`` as the single entry point: it shells out to ``npm test`` and
reports the result as a single test.

Skipped when Node is unavailable or dependencies have not been installed, so the
Python suite still runs on a bare checkout (the frontend tests need
``npm install``; nothing in the Python suite does).
"""
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
JS_TESTS = REPO_ROOT / "tests" / "js"
NODE_MODULES = REPO_ROOT / "node_modules" / "jsdom"


@pytest.mark.skipif(shutil.which("npm") is None, reason="npm is not installed")
@pytest.mark.skipif(
    not NODE_MODULES.exists(),
    reason="frontend test dependencies missing; run `npm install`",
)
def test_frontend_js_suite():
    """``tests/js`` must pass (work-block lifecycle + working indicator)."""
    result = subprocess.run(
        ["npm", "test", "--silent"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    if result.returncode != 0:
        pytest.fail(
            "frontend test suite failed\n"
            f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
        )


def test_frontend_js_tests_are_discovered():
    """Guard against the suite silently testing nothing.

    ``npm test`` globs ``tests/js/``; if a test file is renamed or the glob
    drifts, the runner can exit 0 with zero tests. Assert we have real files.
    """
    files = sorted(JS_TESTS.glob("*.test.js"))
    assert files, f"no frontend test files found in {JS_TESTS}"
    assert (JS_TESTS / "harness.js").exists(), "frontend harness is missing"
