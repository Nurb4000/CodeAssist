"""Tests for the screenshot tool (CDP/headless Chromium driver)."""
import asyncio
import base64
import json
from pathlib import Path

import pytest

from tools.screenshot import ScreenshotTool, _CdpConnection


class _FakeWs:
    """Minimal stand-in for a websockets CDP connection.

    Yields queued JSON strings to the connection's drain task and records every
    sent payload. ``responses`` maps a predicate on the payload -> list of JSON
    strings to enqueue (in response).
    """

    def __init__(self, responses):
        self.responses = responses
        self.sent = []
        self._queue = asyncio.Queue()

    @staticmethod
    def _echo(req_id, text):
        """Rewrite a CDP result message to echo the request id (as real CDP does)."""
        obj = json.loads(text)
        if "id" in obj and "result" in obj:
            obj["id"] = req_id
        return json.dumps(obj)

    async def send(self, data):
        payload = json.loads(data)
        self.sent.append(payload)
        for pred, reply in self.responses:
            if pred(payload):
                for text in reply:
                    await self._queue.put(self._echo(payload.get("id"), text))

    def __aiter__(self):
        return self

    async def __anext__(self):
        item = await self._queue.get()
        if item is None:
            raise StopAsyncIteration
        return item

    async def close(self):
        await self._queue.put(None)


def _reply(req_id, **result):
    """A CDP result message echoing the request id (real CDP does this)."""
    return json.dumps({"id": req_id, "result": result})


def _responses():
    png = base64.b64encode(b"\x89PNG\r\n\x1a\nfake").decode("ascii")

    def is_create(t):
        return t.get("method") == "Target.createTarget"

    def is_attach(t):
        return t.get("method") == "Target.attachToTarget"

    def is_enable(t):
        return t.get("method") == "Page.enable"

    def is_navigate(t):
        return t.get("method") == "Page.navigate"

    def is_capture(t):
        return t.get("method") == "Page.captureScreenshot"

    return [
        (is_create, [_reply(1, targetId="T1")]),
        (is_attach, [_reply(2, sessionId="S1")]),
        (is_enable, [_reply(3)]),
        (
            is_navigate,
            [
                _reply(4),
                json.dumps({"method": "Page.loadEventFired"}),
            ],
        ),
        (is_capture, [_reply(5, data=png)]),
    ]


@pytest.mark.asyncio
async def test_cdp_connection_captures_screenshot():
    ws = _FakeWs(_responses())
    cdp = _CdpConnection(ws)
    cdp.start()
    try:
        target_id = (await cdp.call("Target.createTarget", {"url": "about:blank"}))[
            "targetId"
        ]
        assert target_id == "T1"
        attach = await cdp.call(
            "Target.attachToTarget", {"targetId": target_id, "flatten": True}
        )
        assert attach["sessionId"] == "S1"
        await cdp.call("Page.enable", {}, "S1")
        # navigate should wait for Page.loadEventFired without timing out
        await cdp.navigate("https://example.com", "S1", timeout=5)
        snap = await cdp.call(
            "Page.captureScreenshot", {"format": "png"}, "S1"
        )
        assert base64.b64decode(snap["data"]) == b"\x89PNG\r\n\x1a\nfake"
    finally:
        await cdp.close()

    # Every CDP call should have been sent with the active session id.
    navigate = next(p for p in ws.sent if p.get("method") == "Page.navigate")
    assert navigate["sessionId"] == "S1"


@pytest.mark.asyncio
async def test_cdp_navigate_does_not_hang_when_no_load_event():
    # A ws that replies to navigate but never emits loadEventFired: navigate must
    # still return after the timeout window rather than blocking forever.
    def is_navigate(t):
        return t.get("method") == "Page.navigate"

    ws = _FakeWs(
        [(is_navigate, [json.dumps({"id": 4, "result": {}})])]
    )
    cdp = _CdpConnection(ws)
    cdp.start()
    try:
        started = asyncio.get_running_loop().time()
        await cdp.navigate("https://x", "S1", timeout=0.3)
        elapsed = asyncio.get_running_loop().time() - started
        assert elapsed < 2.0
    finally:
        await cdp.close()


def test_missing_url_and_app_is_error():
    tool = ScreenshotTool()

    async def run():
        return await tool.execute()

    result = asyncio.run(run())
    assert result.error is True
    assert "capture_local_app" in result.output or "url" in result.output


@pytest.mark.asyncio
async def test_capture_local_app_builds_server_url(monkeypatch):
    class _Cfg:
        class _Server:
            host = "127.0.0.1"
            port = 8090

        server = _Server()

    tool = ScreenshotTool()
    url = await tool._resolve_target_url(None, True, _Cfg())
    assert url == "http://127.0.0.1:8090/"


def test_find_browser_returns_string_or_none():
    found = ScreenshotTool._find_browser()
    assert found is None or (isinstance(found, str) and len(found) > 0)


def test_find_browser_respects_monkeypatched_which(monkeypatch):
    monkeypatch.setattr("tools.screenshot.shutil.which", lambda name: None)
    monkeypatch.setattr(Path, "exists", lambda self: False)
    assert ScreenshotTool._find_browser() is None


def test_free_port_is_in_valid_range():
    port = ScreenshotTool._free_port()
    assert 1 <= port <= 65535


@pytest.mark.asyncio
async def test_output_path_outside_workspace_rejected(tmp_path):
    tool = ScreenshotTool()
    tool.workspace = tmp_path
    result = tool._resolve_output_path(
        str(tmp_path.parent / "escape.png"), tmp_path
    )
    assert hasattr(result, "error") and result.error is True


@pytest.mark.asyncio
async def test_default_output_name_is_png(tmp_path):
    tool = ScreenshotTool()
    path = tool._resolve_output_path(None, tmp_path)
    assert isinstance(path, Path)
    assert path.suffix == ".png"
    assert path.name.startswith("screenshot_")


@pytest.mark.asyncio
async def test_maybe_analyze_calls_image_analyze(monkeypatch):
    class _Res:
        output = "analyzed!"

    class _Tool:
        async def execute(self, **kwargs):
            return _Res()

    monkeypatch.setattr("tools.image_analyze.ImageAnalyzeTool", _Tool)
    tool = ScreenshotTool()
    out = await tool._maybe_analyze(Path("/tmp/x.png"), "what?")
    assert "analyzed!" in out


def _has_browser():
    return ScreenshotTool._find_browser() is not None


@pytest.mark.skipif(not _has_browser(), reason="no Chrome/Chromium on PATH")
@pytest.mark.asyncio
async def test_e2e_captures_real_page(tmp_path, monkeypatch):
    """Launch real headless Chrome against a local HTTP server and capture.

    Exercises the full CDP path (browser discovery, endpoint discovery, session
    negotiation, navigation, capture) that pure fake-websocket tests can't.
    """
    import http.server
    import socketserver
    import threading

    html = b"<html><body><h1 id='t'>Screenshot</h1></body></html>"

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(html)

        def log_message(self, *args):
            pass

    monkeypatch.setenv("CODEASSIST_WORKSPACE", str(tmp_path))
    with socketserver.TCPServer(("127.0.0.1", 0), Handler) as srv:
        port = srv.server_address[1]
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        try:
            tool = ScreenshotTool()
            out = tmp_path / "cap.png"
            result = await tool.execute(
                url=f"http://127.0.0.1:{port}/",
                output_path=str(out),
                wait_seconds=1.0,
            )
        finally:
            srv.shutdown()
            thread.join(timeout=5)

    assert result.error is False
    assert out.exists()
    assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
