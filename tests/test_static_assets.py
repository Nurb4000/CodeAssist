"""Guards on the served static assets.

The frontend is served unbundled, so a file that does not parse is not a
degraded page -- it is a page that never runs. A single stray character in
``admin.js`` left the whole registry page rendering its static HTML and nothing
else: no collapsible sections, no sidebar navigation, empty tables. The Python
suite was green throughout, because it never parses the JavaScript.

``node --check`` needs only a Node binary (no ``npm install``), so this runs on
a bare checkout.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = REPO_ROOT / "codeassist" / "static"
# Third-party bundles are not ours to lint, and are already minified.
VENDOR_DIR = STATIC_DIR / "vendor"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None,
    reason="node is not installed",
)


def _own_scripts() -> list[Path]:
    return sorted(p for p in STATIC_DIR.rglob("*.js") if VENDOR_DIR not in p.parents)


@pytest.mark.parametrize("script", _own_scripts(), ids=lambda p: p.name)
def test_static_script_parses(script: Path):
    """Every script we author must be syntactically valid.

    A parse error is silent at import time in the browser -- the whole file is
    discarded and the page falls back to its markup, which reads as a handful of
    unrelated missing features rather than as a broken script.
    """
    result = subprocess.run(
        ["node", "--check", str(script)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, (
        f"{script.relative_to(REPO_ROOT)} does not parse:\n"
        f"{result.stdout}{result.stderr}"
    )


def test_admin_script_is_covered():
    """admin.js must stay in the checked set.

    The admin page is the one that regressed; if it is ever excluded from the
    sweep above (renamed, moved, or the glob narrowed) the guard silently stops
    covering the file that broke.
    """
    names = {p.name for p in _own_scripts()}
    assert "admin.js" in names
