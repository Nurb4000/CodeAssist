"""Tests for the Playwright-backed browser tool.

Unit tests cover the URL policy, schema and config parsing without a browser.
The integration tests drive a real headless browser against a local HTTP server
and are skipped per-engine when that Playwright engine (or a system Chrome for
Chromium) is unavailable.
"""

import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

import codeassist.tools.browser as browser_mod
from codeassist.config import BrowserConfig, Config, _browser_config
from codeassist.tools.browser import (
    BrowserManager,
    BrowserTool,
    _forbidden_subresource_reason,
    validate_browser_url,
)


class _Cfg:
    def __init__(self, **kwargs):
        self.browser = BrowserConfig(**kwargs)
        for key, value in kwargs.items():
            setattr(self, key, value)


# ---------------------------------------------------------------------------
# URL policy (no browser)
# ---------------------------------------------------------------------------


def test_loopback_dev_ports_are_allowed():
    cfg = _Cfg()
    for url in (
        "http://127.0.0.1:5173/",
        "http://localhost:3000/login",
        "http://[::1]:8000/",
        "http://127.0.0.42/",
    ):
        assert validate_browser_url(url, cfg) is None, url


def test_loopback_can_be_disabled():
    cfg = _Cfg(allow_local=False)
    assert validate_browser_url("http://127.0.0.1:5173/", cfg) is not None


def test_disallowed_schemes_are_rejected():
    cfg = _Cfg(allow_public=True)
    for url in (
        "file:///etc/passwd",
        "javascript:alert(1)",
        "data:text/html,<h1>hi</h1>",
        "ftp://example.com/file",
    ):
        assert validate_browser_url(url, cfg) is not None, url


def test_metadata_and_link_local_are_never_allowed():
    cfg = _Cfg(
        allow_public=True,
        allow_local=True,
        allowed_origins=["http://169.254.169.254"],
        allowed_private_hosts=["169.254.169.254", "metadata.google.internal"],
    )
    for url in (
        "http://169.254.169.254/latest/meta-data/",
        "http://metadata.google.internal/",
        "http://[fe80::1]/",
    ):
        assert validate_browser_url(url, cfg) is not None, url


def test_private_and_public_hosts_require_opt_in():
    cfg = _Cfg()
    assert validate_browser_url("http://192.168.1.20:8080/", cfg) is not None
    assert validate_browser_url("https://example.com/", cfg) is not None

    cfg = _Cfg(allow_public=True)
    assert validate_browser_url("https://example.com/", cfg) is None
    # Public opt-in does not silently open the LAN.
    assert validate_browser_url("http://192.168.1.20:8080/", cfg) is not None


def test_allowed_origins_and_private_hosts_are_exact():
    cfg = _Cfg(
        allowed_origins=["http://Example.COM:5173"],
        allowed_private_hosts=["devbox.lan"],
    )
    assert validate_browser_url("http://example.com:5173/app", cfg) is None
    assert validate_browser_url("http://example.com:5174/", cfg) is not None
    assert validate_browser_url("http://devbox.lan:9000/", cfg) is None
    assert validate_browser_url("http://devbox.lan.evil.test/", cfg) is not None


def test_embedded_credentials_are_rejected():
    assert validate_browser_url("http://user:pass@127.0.0.1/", _Cfg()) is not None


def test_subresource_guard_blocks_only_dangerous_targets():
    assert _forbidden_subresource_reason("data:image/png;base64,AAAA") is None
    assert _forbidden_subresource_reason("blob:http://127.0.0.1/x") is None
    assert _forbidden_subresource_reason("https://cdn.example.com/app.js") is None
    assert _forbidden_subresource_reason("http://127.0.0.1:9000/api") is None
    assert _forbidden_subresource_reason("http://169.254.169.254/latest") is not None
    assert _forbidden_subresource_reason("http://[fe80::1]/") is not None
    assert _forbidden_subresource_reason("file:///etc/passwd") is not None


def test_registry_registers_browser_tool(tmp_path):
    from codeassist.tools import create_registry

    registry = create_registry(tmp_path)
    assert "browser" in registry._tools
    assert registry._tools["browser"].workspace == tmp_path


def test_tool_schema_exposes_actions_and_fields():
    schema = BrowserTool.parameters
    assert set(schema["properties"]["action"]["enum"]) == {
        "open", "snapshot", "click", "fill", "type", "press", "hover",
        "select", "check", "wait_for", "screenshot", "evaluate", "console",
        "network", "close",
    }
    assert schema["required"] == ["action"]
    assert schema["additionalProperties"] is False
    for name in ("url", "ref", "selector", "text", "key", "value", "script",
                 "timeout_ms", "full_page", "output_path", "tab"):
        assert name in schema["properties"]


# ---------------------------------------------------------------------------
# Config parsing (no browser)
# ---------------------------------------------------------------------------


def test_browser_config_parsing(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        """
[browser]
enabled = true
headless = false
engine = "firefox"
allow_local = false
allow_public = true
allowed_origins = ["http://host.docker.internal:5173"]
allowed_private_hosts = ["host.docker.internal"]
navigation_timeout_seconds = 60
action_timeout_seconds = 7
idle_timeout_seconds = 120
max_pages = 3
no_sandbox = false
""".strip()
    )
    cfg = Config.load(cfg_path)
    assert cfg.browser.enabled is True
    assert cfg.browser.headless is False
    assert cfg.browser.engine == "firefox"
    assert cfg.browser.allow_local is False
    assert cfg.browser.allow_public is True
    assert cfg.browser.allowed_origins == ["http://host.docker.internal:5173"]
    assert cfg.browser.allowed_private_hosts == ["host.docker.internal"]
    assert cfg.browser.navigation_timeout_seconds == 60
    assert cfg.browser.action_timeout_seconds == 7
    assert cfg.browser.idle_timeout_seconds == 120
    assert cfg.browser.max_pages == 3
    assert cfg.browser.no_sandbox is False


def test_browser_config_defaults_and_normalization():
    cfg = _browser_config({"max_pages": 0, "action_timeout_seconds": "nope"})
    assert cfg.max_pages == 8
    assert cfg.action_timeout_seconds == 15
    assert cfg.engine == "chromium"
    assert cfg.no_sandbox is None
    assert _browser_config({"allowed_origins": "not-a-list"}).allowed_origins == []


def test_browser_config_engine_is_normalized_and_validated():
    assert _browser_config({"engine": " WebKit "}).engine == "webkit"
    with pytest.raises(ValueError, match="browser.engine"):
        _browser_config({"engine": "gecko"})


class _FakeEngine:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def launch(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(version="fake")


class _FakePlaywright:
    def __init__(self) -> None:
        self.chromium = _FakeEngine()
        self.firefox = _FakeEngine()
        self.webkit = _FakeEngine()


@pytest.mark.asyncio
async def test_browser_manager_dispatches_to_configured_engine():
    manager = BrowserManager()
    fake = _FakePlaywright()
    manager._playwright = fake

    await manager._launch("firefox", "", True, [])
    assert fake.firefox.calls == [{"headless": True}]
    assert fake.chromium.calls == [] and fake.webkit.calls == []

    await manager._launch("chromium", "/usr/bin/chrome", False, ["--no-sandbox"])
    assert fake.chromium.calls == [
        {"headless": False, "args": ["--no-sandbox"], "executable_path": "/usr/bin/chrome"}
    ]
    assert fake.firefox.calls == [{"headless": True}]
    assert fake.webkit.calls == []


# ---------------------------------------------------------------------------
# Real-browser integration
# ---------------------------------------------------------------------------

_PAGE = b"""<!doctype html>
<html><head><title>Smoke App</title></head>
<body>
  <h1>Smoke</h1>
  <form id="f">
    <label>Name <input id="name" name="name" aria-label="Name"></label>
    <select id="color" aria-label="Color">
      <option value="red">Red</option>
      <option value="blue">Blue</option>
    </select>
    <input type="checkbox" id="agree" aria-label="Agree">
    <button id="go" type="button">Submit</button>
  </form>
  <p id="out">waiting</p>
  <a href="http://169.254.169.254/">Metadata</a>
  <script>
    console.error('smoke-boot-error');
    fetch('/missing.js').catch(() => {});
    document.getElementById('go').addEventListener('click', () => {
      const name = document.getElementById('name').value;
      document.getElementById('out').textContent = 'hello ' + name;
      localStorage.setItem('smoke', name);
      console.log('submitted ' + name);
    });
  </script>
</body></html>
"""


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/missing.js":
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"not found")
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(_PAGE)

    def log_message(self, *args):
        pass


_ENGINE_GLOBS = {"chromium": "chromium*", "firefox": "firefox*", "webkit": "webkit*"}


def _playwright_has_engine(engine: str) -> bool:
    # Only Chromium has a system-browser fallback.
    if engine == "chromium" and browser_mod._find_system_browser():
        return True
    roots = [
        os.environ.get("PLAYWRIGHT_BROWSERS_PATH"),
        str(Path.home() / ".cache" / "ms-playwright"),
    ]
    for root in roots:
        if root and Path(root).exists() and any(
            Path(root).glob(_ENGINE_GLOBS[engine])
        ):
            return True
    return False


def _has_playwright_package() -> bool:
    try:
        import playwright.async_api  # noqa: F401
    except ImportError:
        return False
    return True


def _ref(output: str, role: str, name: str) -> str:
    match = re.search(rf"\[(e\d+)\] {re.escape(role)} \"{re.escape(name)}\"", output)
    assert match, f"no {role} {name!r} in snapshot:\n{output}"
    return match.group(1)


@pytest.mark.asyncio
@pytest.mark.parametrize("engine", ["chromium", "firefox", "webkit"])
async def test_browser_manager_launches_configured_engine(engine):
    if not _has_playwright_package():
        pytest.skip("Playwright package unavailable")
    if not _playwright_has_engine(engine):
        pytest.skip(f"Playwright {engine} unavailable")

    manager = BrowserManager()
    cfg = BrowserConfig(engine=engine)
    try:
        browser = await manager._ensure_browser(cfg)
        assert browser.is_connected()
        assert manager.running is True
    finally:
        await manager.close_all()

    assert manager.running is False


@pytest.mark.asyncio
@pytest.mark.parametrize("engine", ["chromium", "firefox", "webkit"])
async def test_browser_e2e_flow(engine, tmp_path, monkeypatch):
    if not _has_playwright_package():
        pytest.skip("Playwright package unavailable")
    if not _playwright_has_engine(engine):
        pytest.skip(f"Playwright {engine} unavailable")

    manager = BrowserManager()
    tool = BrowserTool(manager=manager)
    tool.workspace = tmp_path

    cfg = SimpleNamespace(
        browser=BrowserConfig(
            engine=engine, action_timeout_seconds=5, navigation_timeout_seconds=10
        ),
        workspace=tmp_path,
    )
    monkeypatch.setattr(browser_mod, "live_config", lambda: cfg)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{srv.server_address[1]}/"

    try:
        opened = await tool.execute(action="open", url=base)
        assert opened.error is False, opened.output
        assert "Smoke App" in opened.output
        assert "status 200" in opened.output
        assert "smoke-boot-error" in opened.output
        assert "missing.js" in opened.output

        snap = await tool.execute(action="snapshot")
        assert snap.error is False
        name_ref = _ref(snap.output, "textbox", "Name")
        color_ref = _ref(snap.output, "combobox", "Color")
        agree_ref = _ref(snap.output, "checkbox", "Agree")
        button_ref = _ref(snap.output, "button", "Submit")
        meta_ref = _ref(snap.output, "link", "Metadata")

        filled = await tool.execute(action="fill", ref=name_ref, text="Ada")
        assert filled.error is False
        assert "Value: 'Ada'" in filled.output

        selected = await tool.execute(action="select", ref=color_ref, value="blue")
        assert selected.error is False
        assert "['blue']" in selected.output

        checked = await tool.execute(action="check", ref=agree_ref)
        assert checked.error is False
        assert "Checked: True" in checked.output

        clicked = await tool.execute(action="click", ref=button_ref)
        assert clicked.error is False, clicked.output
        assert "hello Ada" in clicked.output

        waited = await tool.execute(action="wait_for", text="hello Ada")
        assert waited.error is False, waited.output

        stored = await tool.execute(action="evaluate", script="localStorage.getItem('smoke')")
        assert stored.error is False
        assert '"Ada"' in stored.output

        logs = await tool.execute(action="console")
        assert logs.error is False
        assert "smoke-boot-error" in logs.output
        assert "submitted Ada" in logs.output

        network = await tool.execute(action="network")
        assert network.error is False
        assert "missing.js" in network.output
        assert "404" in network.output

        shot_path = tmp_path / "shot.png"
        shot = await tool.execute(
            action="screenshot", output_path="shot.png", full_page=True
        )
        assert shot.error is False, shot.output
        assert shot_path.exists()
        assert shot_path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"

        outside = await tool.execute(action="screenshot", output_path="../escape.png")
        assert outside.error is True
        assert not (tmp_path.parent / "escape.png").exists()

        failed = await tool.execute(action="wait_for", selector="#never", timeout_ms=250)
        assert failed.error is True
        assert "Timeout" in failed.output or "timeout" in failed.output
        assert "Failure screenshot:" in failed.output
        failure_png = Path(
            failed.output.split("Failure screenshot:", 1)[1].splitlines()[0].strip()
        )
        assert failure_png.exists()

        blocked = await tool.execute(action="click", ref=meta_ref)
        assert blocked.error is True, blocked.output
        assert "169.254.169.254" in blocked.output
        assert "blocked" in blocked.output.lower()

        closed = await tool.execute(action="close")
        assert closed.error is False
        assert manager.running is True  # browser process stays warm
        assert manager._sessions == {}
    finally:
        await manager.close_all()
        srv.shutdown()
        thread.join(timeout=5)

    assert manager.running is False
