"""Screenshot Tool - capture a page as a PNG by driving headless Chromium over CDP.

Drives a system Chrome/Chromium with the DevTools Protocol (via the `websockets`
client, already a project dependency): opens the target URL, waits for the page to
load, and captures a PNG viewport screenshot into the workspace. The same CDP
driver can be extended later for click/DOM-check verification.

Chromium is optional: the tool fails gracefully with a clear "chromium not found"
message when no supported browser binary is on PATH (e.g. a minimal Docker image),
so minimal images don't pay the ~150MB+ cost unless the user opts in.
"""

import asyncio
import base64
import json
import logging
import shutil
import socket
import subprocess
from datetime import datetime
from pathlib import Path

from tools import Tool, ToolResult
from tools.security import validate_path

log = logging.getLogger(__name__)

# Browser binaries tried (in order) via `shutil.which`; macOS bundled binary
# checked separately.
BROWSER_CANDIDATES = [
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
    "chrome",
]
MAC_BROWSER = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

# Extra settle time (seconds) after loadEvent before capturing, so late JS /
# fonts / lazy images have a chance to render.
DEFAULT_WAIT_SECONDS = 0.5
LOAD_TIMEOUT = 45.0
CDP_CONNECT_TIMEOUT = 12.0


class ScreenshotTool(Tool):
    name = "screenshot"
    description = (
        "Capture a screenshot (PNG) of a web page by driving headless Chromium via "
        "the DevTools Protocol (CDP). Pass a URL, or set capture_local_app to capture "
        "the running CodeAssist app from config. Saves a PNG in the workspace and "
        "returns its path; optionally send the image through image_analyze with "
        "question."
    )
    parameters = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "Page URL to capture. Omit and set capture_local_app=true "
                "to capture the running CodeAssist UI.",
            },
            "capture_local_app": {
                "type": "boolean",
                "description": "If true, capture the local CodeAssist app (from [server] "
                "host/port) instead of an external URL.",
            },
            "output_path": {
                "type": "string",
                "description": "Where to save the PNG (must be inside the workspace). "
                "Defaults to screenshot_<timestamp>.png in the workspace.",
            },
            "width": {
                "type": "integer",
                "description": "Viewport width in pixels (default 1280).",
            },
            "height": {
                "type": "integer",
                "description": "Viewport height in pixels (default 800).",
            },
            "wait_seconds": {
                "type": "number",
                "description": "Extra seconds to wait after page load before capturing "
                "(default 0.5). Increase for slow/lazy-loading pages.",
            },
            "analyze": {
                "type": "boolean",
                "description": "If true, run the captured image through image_analyze "
                "with the given question and append the result.",
            },
            "question": {
                "type": "string",
                "description": "Question for image_analyze when analyze=true "
                "(default: describe the screenshot).",
            },
        },
        "required": [],
        "additionalProperties": False,
    }

    DEFAULT_WIDTH = 1280
    DEFAULT_HEIGHT = 800

    def __init__(self):
        self.workspace = Path.cwd()

    async def execute(
        self,
        url: str | None = None,
        capture_local_app: bool = False,
        output_path: str | None = None,
        width: int | None = None,
        height: int | None = None,
        wait_seconds: float | None = None,
        analyze: bool = False,
        question: str | None = None,
    ) -> ToolResult:
        try:
            from codeassist.config import load_config

            config = load_config()

            target_url = await self._resolve_target_url(url, capture_local_app, config)
            if isinstance(target_url, ToolResult):
                return target_url

            png_path = self._resolve_output_path(output_path, config.workspace)
            if isinstance(png_path, ToolResult):
                return png_path

            width = int(width or self.DEFAULT_WIDTH)
            height = int(height or self.DEFAULT_HEIGHT)
            wait = float(wait_seconds if wait_seconds is not None else DEFAULT_WAIT_SECONDS)

            browser = self._find_browser()
            if not browser:
                return ToolResult(
                    output=(
                        "Error: no supported Chromium/Chrome browser found on PATH "
                        f"(tried {', '.join(BROWSER_CANDIDATES)} and {MAC_BROWSER}). "
                        "Install Chrome/Chromium or run inside an image that has it."
                    ),
                    error=True,
                )

            png_bytes = await self._capture(
                browser, target_url, width, height, wait
            )
            if isinstance(png_bytes, ToolResult):
                return png_bytes

            png_path.parent.mkdir(parents=True, exist_ok=True)
            png_path.write_bytes(png_bytes)

            size_mb = len(png_bytes) / (1024 * 1024)
            result_text = (
                f"**Screenshot saved** ({png_path.name}, {size_mb:.1f}MB) at "
                f"{png_path}\nURL: {target_url}"
            )

            if analyze:
                result_text += await self._maybe_analyze(png_path, question)

            return ToolResult(output=result_text)

        except Exception as e:
            log.exception("screenshot failed")
            return ToolResult(output=f"Error: {e}", error=True)

    async def _resolve_target_url(self, url, capture_local_app, config):
        if capture_local_app:
            host = config.server.host or "127.0.0.1"
            # The app listens on the server port; use http:// over loopback.
            scheme = "http"
            return f"{scheme}://{host}:{config.server.port}/"
        if url:
            return url
        return ToolResult(
            output="Error: provide either 'url' or capture_local_app=true.",
            error=True,
        )

    def _resolve_output_path(self, output_path, workspace):
        base = workspace.resolve()
        if output_path:
            try:
                return validate_path(output_path, base)
            except Exception as e:
                return ToolResult(output=f"Error: {e}", error=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return base / f"screenshot_{stamp}.png"

    @staticmethod
    def _find_browser() -> str | None:
        for name in BROWSER_CANDIDATES:
            path = shutil.which(name)
            if path:
                return path
        mac = Path(MAC_BROWSER)
        if mac.exists():
            return str(mac)
        return None

    @staticmethod
    def _free_port() -> int:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        return port

    async def _capture(self, browser, url, width, height, wait):
        port = self._free_port()
        cmd = [
            browser,
            "--headless=new",
            "--no-sandbox",
            "--disable-gpu",
            "--disable-dev-shm-usage",
            f"--remote-debugging-port={port}",
            f"--window-size={width},{height}",
            "about:blank",
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        try:
            http_base = f"http://127.0.0.1:{port}"
            ws = await self._connect_cdp(http_base)
            if ws is None:
                return ToolResult(
                    output=(
                        "Error: could not connect to Chromium's DevTools endpoint. "
                        "Is a headless Chrome/Chromium available?"
                    ),
                    error=True,
                )
            try:
                cdp = _CdpConnection(ws)
                cdp.start()
                target_id = (await cdp.call("Target.createTarget", {"url": "about:blank"}))[
                    "targetId"
                ]
                attach = await cdp.call(
                    "Target.attachToTarget",
                    {"targetId": target_id, "flatten": True},
                )
                session_id = attach["sessionId"]
                await cdp.call("Page.enable", {}, session_id)
                await cdp.navigate(url, session_id, timeout=LOAD_TIMEOUT)
                if wait > 0:
                    await asyncio.sleep(wait)
                snap = await cdp.call(
                    "Page.captureScreenshot", {"format": "png"}, session_id
                )
                return base64.b64decode(snap["data"])
            finally:
                await cdp.close()
        finally:
            await self._terminate(proc)

    @staticmethod
    async def _terminate(proc):
        try:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
        except ProcessLookupError:
            pass

    async def _connect_cdp(self, http_base):
        """Connect to Chromium's DevTools websocket.

        Chrome exposes the browser-level WS endpoint at a path that includes a
        per-run UUID; we discover it via `<base>/json/version` (which is more
        robust than guessing `/devtools/browser`) and retry until it appears,
        since the endpoint is not available immediately after launch.
        """
        try:
            from websockets.asyncio.client import connect as ws_connect
        except Exception:  # noqa: BLE001 - older websockets expose connect at top level
            import websockets
            ws_connect = websockets.connect
        last_err = None
        for _ in range(int(CDP_CONNECT_TIMEOUT / 0.4)):
            try:
                ws_url = self._devtools_ws_url(http_base)
                if not ws_url:
                    raise RuntimeError("cdp not ready yet")
                return await asyncio.wait_for(
                    ws_connect(ws_url, max_size=None),
                    timeout=0.4,
                )
            except Exception as e:  # noqa: BLE001 - retry any connect failure
                last_err = e
                await asyncio.sleep(0.4)
        log.debug("CDP connect failed after retries: %s", last_err)
        return None

    @staticmethod
    def _devtools_ws_url(http_base: str) -> str | None:
        version = ScreenshotTool._get_json(http_base + "/json/version")
        if not version:
            return None
        return version.get("webSocketDebuggerUrl")

    @staticmethod
    def _get_json(url: str, timeout: float = 2.0) -> dict | None:
        import http.client
        from urllib.parse import urlparse

        parsed = urlparse(url)
        path = parsed.path or "/json/version"
        try:
            conn = http.client.HTTPConnection(parsed.netloc.split(":")[0],
                                              int(parsed.netloc.split(":")[1] or 80),
                                              timeout=timeout)
            conn.request("GET", path)
            resp = conn.getresponse()
            data = resp.read()
            conn.close()
        except Exception:  # noqa: BLE001 - endpoint not up yet, caller retries
            return None
        try:
            return json.loads(data)
        except (ValueError, TypeError):
            return None

    async def _maybe_analyze(self, png_path, question):
        try:
            from tools.image_analyze import ImageAnalyzeTool

            tool = ImageAnalyzeTool()
            res = await tool.execute(
                file_path=str(png_path),
                question=question or "Describe this screenshot and note any UI issues.",
            )
            return f"\n\n{res.output}"
        except Exception as e:
            log.debug("screenshot analyze failed: %s", e)
            return "\n\n(image_analyze skipped: {})".format(e)


class _CdpConnection:
    """Minimal DevTools Protocol client over an already-connected websocket.

    The websocket must be an async iterator yielding JSON strings and expose
    ``send``/``close`` coroutines (as ``websockets`` connections do).
    """

    def __init__(self, ws):
        self.ws = ws
        self._next_id = 1
        self._futures: dict[int, asyncio.Future] = {}
        self._event_futures: list[tuple[str, asyncio.Future]] = []
        self._drain_task = None
        self._closed = False

    def start(self):
        self._drain_task = asyncio.create_task(self._drain())

    async def _drain(self):
        try:
            async for raw in self.ws:
                # websockets >=14 yields parsed ServerMessage objects; older
                # versions yield raw strings. Normalise to the payload text.
                text = getattr(raw, "data", raw)
                try:
                    msg = json.loads(text)
                except (ValueError, TypeError):
                    continue
                req_id = msg.get("id")
                if req_id in self._futures:
                    fut = self._futures.pop(req_id)
                    if not fut.done():
                        fut.set_result(msg)
                method = msg.get("method")
                if method:
                    for i, (m, fut) in enumerate(self._event_futures):
                        if m == method and not fut.done():
                            fut.set_result(msg)
                            self._event_futures.pop(i)
        except Exception:  # noqa: BLE001 - connection closed; drain ends
            pass

    async def call(self, method, params=None, session_id=None):
        cid = self._next_id
        self._next_id += 1
        fut = asyncio.get_running_loop().create_future()
        self._futures[cid] = fut
        payload = {"id": cid, "method": method, "params": params or {}}
        if session_id:
            payload["sessionId"] = session_id
        await self.ws.send(json.dumps(payload))
        resp = await fut
        return resp.get("result", {})

    def wait_for_event(self, method):
        fut = asyncio.get_running_loop().create_future()
        self._event_futures.append((method, fut))
        return fut

    async def navigate(self, url, session_id, timeout):
        await self.call("Page.navigate", {"url": url}, session_id)
        try:
            await asyncio.wait_for(self.wait_for_event("Page.loadEventFired"), timeout)
        except asyncio.TimeoutError:
            # Still usable; a slow page may have partially rendered.
            pass

    async def close(self):
        if self._closed:
            return
        self._closed = True
        # Best-effort detach so the browser target is cleaned up.
        try:
            await self.ws.send(
                json.dumps({"method": "Target.detachFromTarget", "sessionId": 0})
            )
        except Exception:  # noqa: BLE001
            pass
        # Close the transport first so the drain task's async-for ends cleanly
        # (rather than needing to be cancelled mid-await).
        try:
            await self.ws.close()
        except Exception:  # noqa: BLE001
            pass
        if self._drain_task is not None:
            try:
                await asyncio.wait_for(self._drain_task, timeout=2.0)
            except Exception:  # noqa: BLE001
                pass
