"""Browser Tool — drive a headless browser for UI inspection and testing.

This is the interactive counterpart to :mod:`screenshot`. It keeps a Playwright
browser and one context per CodeAssist session alive between tool calls, so a
model can open a local dev server, inspect the accessibility/DOM state, fill
forms, click, and read console/network errors as one flow.

Security notes:
  * URL policy is deliberately separate from ``security.validate_url`` (used by
    webfetch/http). Loopback dev servers are allowed by default; LAN/private and
    public hosts require explicit ``[browser]`` opt-in.
  * The browser/context is per CodeAssist session, so cookies/localStorage do
    not leak between conversations.
  * ``evaluate`` runs page JavaScript. It is powerful by design; restrict the
    tool through agent permissions if read-only agents should not use it.

Playwright is optional: importing this module never imports ``playwright`` and
the tool returns an actionable install message if the package is absent.
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import json
import logging
import os
import shutil
import time
from collections import deque
from collections.abc import Iterator
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from . import Tool, ToolResult, live_config, tool_error
from .security import WorkspaceViolationError, validate_path

log = logging.getLogger(__name__)

# Interactive element selector. Kept in JS so the snapshot reflects the *live*
# DOM rather than a possibly stale server-side copy.
_SNAPSHOT_SCRIPT = r"""
() => {
  const MAX_ELEMENTS = 120;
  const MAX_NAME = 120;
  const MAX_TEXT = 6000;

  document.querySelectorAll('[data-ca-ref]').forEach((el) => {
    el.removeAttribute('data-ca-ref');
  });

  const visible = (el) => {
    const style = window.getComputedStyle(el);
    if (!style || style.display === 'none' || style.visibility === 'hidden'
        || style.visibility === 'collapse' || Number(style.opacity) === 0) {
      return false;
    }
    const rect = el.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  };

  const clean = (value) => String(value || '')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, MAX_NAME);

  const roleOf = (el) => {
    const explicit = clean(el.getAttribute('role'));
    if (explicit) return explicit;
    const tag = el.tagName.toLowerCase();
    if (tag === 'a') return 'link';
    if (tag === 'button') return 'button';
    if (tag === 'select') return 'combobox';
    if (tag === 'textarea') return 'textbox';
    if (tag === 'input') {
      const type = (el.getAttribute('type') || 'text').toLowerCase();
      if (type === 'checkbox') return 'checkbox';
      if (type === 'radio') return 'radio';
      if (type === 'button' || type === 'submit' || type === 'reset') return 'button';
      if (type === 'range') return 'slider';
      if (type === 'search') return 'searchbox';
      if (type === 'hidden') return 'hidden';
      return 'textbox';
    }
    if (el.isContentEditable) return 'textbox';
    return 'element';
  };

  const nameOf = (el) => {
    const aria = clean(el.getAttribute('aria-label'));
    if (aria) return aria;
    const labelledBy = el.getAttribute('aria-labelledby');
    if (labelledBy) {
      const parts = labelledBy.split(/\s+/)
        .map((id) => document.getElementById(id))
        .filter(Boolean)
        .map((node) => clean(node.innerText || node.textContent));
      const label = clean(parts.join(' '));
      if (label) return label;
    }
    if (el.labels && el.labels.length) {
      const label = clean(Array.from(el.labels).map((l) => l.innerText || l.textContent).join(' '));
      if (label) return label;
    }
    const placeholder = clean(el.getAttribute('placeholder'));
    if (placeholder) return placeholder;
    const title = clean(el.getAttribute('title'));
    if (title) return title;
    const alt = clean(el.getAttribute('alt'));
    if (alt) return alt;
    const text = clean(el.innerText || el.textContent);
    if (text) return text;
    return clean(el.getAttribute('name'));
  };

  const valueOf = (el) => {
    const tag = el.tagName.toLowerCase();
    if (tag === 'select') {
      return Array.from(el.selectedOptions || []).map((o) => o.value).join(',');
    }
    if (tag === 'input' && (el.type === 'checkbox' || el.type === 'radio')) {
      return el.value;
    }
    if ('value' in el) return clean(el.value);
    return '';
  };

  const selector = [
    'a[href]', 'button', 'input:not([type="hidden"])', 'select', 'textarea',
    '[role="button"]', '[role="link"]', '[role="checkbox"]', '[role="radio"]',
    '[role="tab"]', '[role="menuitem"]', '[role="switch"]', '[role="combobox"]',
    '[role="textbox"]', '[contenteditable="true"]',
    '[tabindex]:not([tabindex="-1"])',
  ].join(',');

  const items = [];
  let ref = 0;
  for (const el of document.querySelectorAll(selector)) {
    if (items.length >= MAX_ELEMENTS) break;
    if (!visible(el)) continue;
    ref += 1;
    const id = 'e' + ref;
    el.setAttribute('data-ca-ref', id);
    items.push({
      ref: id,
      role: roleOf(el),
      name: nameOf(el),
      tag: el.tagName.toLowerCase(),
      type: clean(el.getAttribute('type')),
      value: valueOf(el),
      checked: el.checked === true,
      disabled: el.disabled === true || el.getAttribute('aria-disabled') === 'true',
      href: clean(el.getAttribute('href')),
    });
  }

  return {
    url: location.href,
    title: document.title,
    items: items,
    text: (document.body ? (document.body.innerText || '') : '')
      .replace(/\n{3,}/g, '\n\n')
      .trim()
      .slice(0, MAX_TEXT),
  };
}
"""

_BROWSER_CANDIDATES = [
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
    "chrome",
]
_MAC_BROWSER = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

# Hosts/IPs that are never reachable, even if a user allow-lists a private host.
_METADATA_HOSTS = {
    "metadata.google.internal",
    "metadata.goog",
    "instance-data",
}
_METADATA_IPS = {
    ipaddress.ip_address("169.254.169.254"),  # AWS/GCP/Azure metadata
    ipaddress.ip_address("169.254.170.2"),    # ECS task metadata
    ipaddress.ip_address("100.100.100.200"),  # Alibaba Cloud metadata
    ipaddress.ip_address("fd00:ec2::254"),    # AWS IPv6 metadata
}
_PRIVATE_SUFFIXES = (
    ".local",
    ".localhost",
    ".internal",
    ".lan",
    ".home",
    ".intranet",
    ".corp",
)


class BrowserUnavailable(RuntimeError):
    """Playwright or the configured browser engine is not installed."""


class BrowserActionError(RuntimeError):
    """A browser action failed in a way the model can correct."""


_active_session: ContextVar[str | None] = ContextVar(
    "browser_active_session", default=None
)


# ---------------------------------------------------------------------------
# URL policy
# ---------------------------------------------------------------------------


def _origin_key(url: str) -> str | None:
    """Return a normalized ``scheme://host:port`` key, or None if unparseable."""
    value = (url or "").strip()
    if not value:
        return None
    if "://" not in value:
        value = "http://" + value
    try:
        parsed = urlsplit(value)
        scheme = (parsed.scheme or "").lower()
        host = (parsed.hostname or "").lower().rstrip(".")
        port = parsed.port
    except (TypeError, ValueError):
        return None
    if scheme not in ("http", "https") or not host:
        return None
    if port is None:
        port = 443 if scheme == "https" else 80
    host_part = f"[{host}]" if ":" in host else host
    default_port = 443 if scheme == "https" else 80
    return f"{scheme}://{host_part}" + ("" if port == default_port else f":{port}")


def _host_from_url(url: str) -> str | None:
    try:
        parsed = urlsplit(url.strip())
    except (TypeError, ValueError):
        return None
    host = (parsed.hostname or "").lower().rstrip(".")
    return host or None


def _is_metadata_host(host: str) -> bool:
    if host in _METADATA_HOSTS:
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    if ip in _METADATA_IPS:
        return True
    mapped = getattr(ip, "ipv4_mapped", None)
    return bool(mapped and mapped in _METADATA_IPS)


def validate_browser_url(url: str, config: Any) -> str | None:
    """Validate a URL for the browser tool.

    Returns ``None`` when allowed, otherwise a human-readable rejection reason.
    The policy is intentionally independent of ``security.validate_url``: this
    tool exists to reach local dev servers, which the general SSRF guard blocks.
    """
    raw = (url or "").strip()
    if not raw:
        return "URL is empty"

    try:
        parsed = urlsplit(raw)
        scheme = (parsed.scheme or "").lower()
        host = (parsed.hostname or "").lower().rstrip(".")
        port = parsed.port
    except (TypeError, ValueError) as exc:
        return f"Invalid URL: {exc}"

    if scheme not in ("http", "https"):
        return "URL scheme must be http:// or https:// (file:, data:, javascript: are blocked)"
    if not host:
        return "URL has no host"
    if parsed.username or parsed.password:
        return "URLs with embedded credentials are not supported"
    if port is not None and not (0 < port < 65536):
        return "URL has an invalid port"

    if _is_metadata_host(host):
        return f"Host '{host}' is a cloud metadata address and is never allowed"

    # Never allow link-local, regardless of allow_local/allowlist settings.
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None:
        mapped = getattr(ip, "ipv4_mapped", None) or ip
        if mapped.is_link_local:
            return f"Host '{host}' is link-local and is never allowed"

    origins = getattr(config, "allowed_origins", []) or []
    allowed_origins = {key for key in (_origin_key(o) for o in origins) if key}
    if _origin_key(raw) in allowed_origins:
        return None

    private_hosts = {
        str(h).strip().lower().rstrip(".")
        for h in (getattr(config, "allowed_private_hosts", []) or [])
        if str(h).strip()
    }
    if host in private_hosts:
        return None

    def is_loopback(value: str) -> bool:
        if value == "localhost" or value.endswith(".localhost"):
            return True
        try:
            addr = ipaddress.ip_address(value)
        except ValueError:
            return False
        mapped = getattr(addr, "ipv4_mapped", None) or addr
        return mapped.is_loopback or str(mapped) == "0.0.0.0"

    if is_loopback(host):
        if bool(getattr(config, "allow_local", True)):
            return None
        return "Loopback URLs are disabled by browser.allow_local=false"

    if ip is not None:
        mapped = getattr(ip, "ipv4_mapped", None) or ip
        if mapped.is_private or mapped.is_reserved:
            return (
                f"Host '{host}' is on a private/reserved network. Add it to "
                "browser.allowed_private_hosts or browser.allowed_origins to permit it."
            )
        if mapped.is_multicast or mapped.is_unspecified:
            return f"Host '{host}' is not routable"
    elif host.endswith(_PRIVATE_SUFFIXES):
        return (
            f"Host '{host}' looks like a private/LAN host. Add it to "
            "browser.allowed_private_hosts or browser.allowed_origins to permit it."
        )

    if bool(getattr(config, "allow_public", False)):
        return None
    return (
        "Public URLs are disabled by default (browser.allow_public=false). "
        "Add the exact origin to browser.allowed_origins or set allow_public=true."
    )


def _forbidden_subresource_reason(url: str) -> str | None:
    """Block subresources that could reach metadata/link-local/local files.

    Page subresources (scripts, images, fetch) are normally allowed to public or
    LAN origins because local dev apps legitimately use CDNs and sibling dev
    servers. Metadata/link-local hosts and non-web schemes are never allowed,
    and main-frame navigations are still checked with the full URL policy.
    """
    value = (url or "").strip()
    if value.startswith(("data:", "blob:", "about:")):
        return None
    try:
        parsed = urlsplit(value)
        scheme = (parsed.scheme or "").lower()
        host = (parsed.hostname or "").lower().rstrip(".")
    except (TypeError, ValueError):
        return "invalid URL"
    if scheme not in ("http", "https"):
        return f"scheme '{scheme or '?'}' is not allowed"
    if _is_metadata_host(host):
        return f"host '{host}' is a cloud metadata address"
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return None
    mapped = getattr(ip, "ipv4_mapped", None) or ip
    if mapped.is_link_local:
        return f"host '{host}' is link-local"
    return None


# ---------------------------------------------------------------------------
# Browser lifecycle
# ---------------------------------------------------------------------------


@dataclass
class _PageState:
    id: str
    page: Any
    refs: dict[str, str] = field(default_factory=dict)
    console: deque = field(default_factory=lambda: deque(maxlen=200))
    network: deque = field(default_factory=lambda: deque(maxlen=200))
    pending: dict[Any, dict] = field(default_factory=dict)
    seq: int = 0
    status: int | None = None
    title: str = ""
    last_used: float = field(default_factory=time.monotonic)

    def next_seq(self) -> int:
        self.seq += 1
        return self.seq

    def add_console(self, level: str, text: str, location: str = "") -> None:
        self.console.append({
            "seq": self.next_seq(),
            "level": level,
            "text": str(text)[:2000],
            "location": str(location)[:300],
        })

    def add_network(self, kind: str, method: str, url: str, **extra: Any) -> None:
        entry = {
            "seq": self.next_seq(),
            "kind": kind,
            "method": method,
            "url": str(url)[:1000],
        }
        entry.update(extra)
        self.network.append(entry)

    def events_since(self, cursor: int) -> tuple[list[dict], list[dict]]:
        console = [e for e in self.console if e["seq"] > cursor]
        network = [e for e in self.network if e["seq"] > cursor]
        return console, network


class _SessionState:
    def __init__(self, session_id: str, context: Any) -> None:
        self.session_id = session_id
        self.context = context
        self.pages: dict[str, _PageState] = {}
        self.active: str | None = None
        self._next_page = 0
        self.lock = asyncio.Lock()
        self.last_used = time.monotonic()

    def new_page_id(self) -> str:
        self._next_page += 1
        return f"p{self._next_page}"

    def add_page(self, page: Any) -> _PageState:
        state = _PageState(id=self.new_page_id(), page=page)
        self.pages[state.id] = state
        self.active = state.id
        return state

    def get_page(self, tab: str | None = None) -> _PageState | None:
        if tab:
            return self.pages.get(tab)
        if self.active and self.active in self.pages:
            return self.pages[self.active]
        if self.pages:
            self.active = next(iter(self.pages))
            return self.pages[self.active]
        return None

    def remove_page(self, page_id: str) -> None:
        self.pages.pop(page_id, None)
        if self.active == page_id:
            self.active = next(iter(self.pages), None)


class BrowserManager:
    """Lazy process-wide Playwright browser with per-session contexts."""

    def __init__(self) -> None:
        self._playwright: Any = None
        self._browser: Any = None
        self._sessions: dict[str, _SessionState] = {}
        self._launch_lock = asyncio.Lock()
        self._headed_warned = False

    @property
    def running(self) -> bool:
        return bool(self._browser and self._browser.is_connected())

    async def _ensure_browser(self, cfg: Any) -> Any:
        if self.running:
            return self._browser

        async with self._launch_lock:
            if self.running:
                return self._browser

            # A crashed/closed browser invalidates every context/page we held.
            if self._browser is not None and not self._browser.is_connected():
                await self._clear_dead_browser()
            else:
                self._browser = None

            engine = str(getattr(cfg, "engine", "chromium") or "chromium").lower()
            if engine not in ("chromium", "firefox", "webkit"):
                raise BrowserUnavailable(
                    f"Unknown browser engine '{engine}'; "
                    "expected chromium, firefox, or webkit"
                )

            try:
                from playwright.async_api import async_playwright
            except ImportError as exc:  # pragma: no cover - environment dependent
                raise BrowserUnavailable(
                    "The 'browser' tool requires the Playwright Python package. "
                    "Install it with: pip install 'codeassist[browser]' "
                    "(or pip install playwright) and run: "
                    f"playwright install {engine}"
                ) from exc

            if self._playwright is None:
                self._playwright = await async_playwright().start()

            # Chromium-only flags. Firefox/WebKit reject most of these, and the
            # sandbox handling below is Chromium-specific.
            args: list[str] = []
            if engine == "chromium":
                args = ["--disable-dev-shm-usage", "--disable-gpu"]
                no_sandbox = getattr(cfg, "no_sandbox", None)
                if no_sandbox is None:
                    no_sandbox = hasattr(os, "geteuid") and os.geteuid() == 0
                if no_sandbox:
                    args.append("--no-sandbox")

            headless = bool(getattr(cfg, "headless", True))
            executable = (
                getattr(cfg, "executable_path", "")
                or os.environ.get("CODEASSIST_BROWSER_PATH", "")
                or ""
            )

            try:
                browser = await self._launch(engine, executable, headless, args)
            except Exception as exc:
                if executable:
                    raise BrowserUnavailable(
                        f"Could not launch {engine} at '{executable}': {exc}"
                    ) from exc
                # Only Chromium has a system-browser fallback; Firefox/WebKit
                # must come from Playwright's own install.
                if engine != "chromium":
                    raise BrowserUnavailable(
                        f"Playwright could not launch its {engine} build. Run: "
                        f"playwright install {engine} ({exc})"
                    ) from exc
                system = _find_system_browser()
                if not system:
                    raise BrowserUnavailable(
                        "Playwright could not launch its Chromium build and no system "
                        "Chrome/Chromium was found. Run: playwright install chromium "
                        "(or set browser.executable_path)."
                    ) from exc
                log.debug("Playwright browser missing; falling back to %s", system)
                browser = await self._launch(engine, system, headless, args)

            self._browser = browser
            if not headless and not self._headed_warned:
                log.warning("browser.headless=false: a visible browser window will be used")
                self._headed_warned = True
            log.debug(
                "browser launched (engine=%s, version=%s, headless=%s, executable=%s)",
                engine,
                browser.version,
                headless,
                executable or "playwright-managed",
            )
            return browser

    async def _launch(
        self, engine: str, executable: str, headless: bool, args: list[str]
    ) -> Any:
        kwargs: dict[str, Any] = {"headless": headless}
        if args:
            kwargs["args"] = args
        if executable:
            kwargs["executable_path"] = executable
        return await getattr(self._playwright, engine).launch(**kwargs)

    async def session(self, session_id: str, cfg: Any) -> _SessionState:
        await self._cleanup_idle(cfg)
        state = self._sessions.get(session_id)
        if state is None:
            browser = await self._ensure_browser(cfg)
            context = await browser.new_context(
                viewport={
                    "width": int(getattr(cfg, "viewport_width", 1280)),
                    "height": int(getattr(cfg, "viewport_height", 800)),
                },
                accept_downloads=False,
                service_workers="block",
            )
            state = _SessionState(session_id, context)
            self._sessions[session_id] = state

            async def _guard(route: Any, request: Any) -> None:
                await self._guard_request(route, request, state, cfg)

            with contextlib.suppress(Exception):
                await context.route("**/*", _guard)
        state.last_used = time.monotonic()
        return state

    async def _guard_request(
        self, route: Any, request: Any, session: _SessionState, cfg: Any
    ) -> None:
        """Enforce the browser URL policy on navigations and dangerous resources.

        ``open`` validates the requested URL, but a page can later navigate via a
        link or script. Main-frame navigations therefore re-run the full policy;
        subresources only block metadata/link-local/local-file targets so normal
        dev pages can still use CDNs and sibling servers.
        """
        page_state: _PageState | None = None
        reason: str | None = None
        try:
            page = request.frame.page
            page_state = next(
                (ps for ps in session.pages.values() if ps.page is page), None
            )
            is_main_navigation = (
                request.is_navigation_request()
                and request.frame == page.main_frame
            )
            reason = (
                validate_browser_url(request.url, cfg)
                if is_main_navigation
                else _forbidden_subresource_reason(request.url)
            )
        except Exception:  # noqa: BLE001 - worker/etc. has no frame; allow it
            reason = None

        if reason:
            if page_state is not None:
                page_state.add_network(
                    "blocked", request.method, request.url, failure=reason
                )
                with contextlib.suppress(Exception):
                    if request.is_navigation_request():
                        page_state.status = None
            log.warning("browser blocked request %s: %s", request.url, reason)
            with contextlib.suppress(Exception):
                await route.abort()
            return
        with contextlib.suppress(Exception):
            await route.continue_()

    async def _cleanup_idle(self, cfg: Any) -> None:
        idle = max(1, int(getattr(cfg, "idle_timeout_seconds", 900)))
        now = time.monotonic()
        stale = [
            sid for sid, state in self._sessions.items()
            if now - state.last_used > idle and not state.lock.locked()
        ]
        for sid in stale:
            log.debug("Closing idle browser session %s", sid)
            await self._close_session_state(sid)

    async def _close_session_state(self, session_id: str) -> None:
        state = self._sessions.pop(session_id, None)
        if state is None:
            return
        with contextlib.suppress(Exception):
            await state.context.close()

    async def close_session(self, session_id: str) -> None:
        await self._close_session_state(session_id)

    async def _clear_dead_browser(self) -> None:
        self._sessions.clear()
        self._browser = None

    async def close_all(self) -> None:
        for session_id in list(self._sessions):
            await self._close_session_state(session_id)
        if self._browser is not None:
            with contextlib.suppress(Exception):
                await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            with contextlib.suppress(Exception):
                await self._playwright.stop()
            self._playwright = None


_manager: BrowserManager | None = None


def get_browser_manager() -> BrowserManager:
    global _manager
    if _manager is None:
        _manager = BrowserManager()
    return _manager


async def shutdown_browser() -> None:
    """Close the process-wide browser. Safe to call when never started."""
    global _manager
    manager = _manager
    _manager = None
    if manager is not None:
        await manager.close_all()


def _find_system_browser() -> str | None:
    for name in _BROWSER_CANDIDATES:
        path = shutil.which(name)
        if path:
            return path
    if Path(_MAC_BROWSER).exists():
        return _MAC_BROWSER
    return None


def _timestamp() -> str:
    # Milliseconds avoid two failures/screenshots in the same second overwriting
    # each other (and make failure artifacts easy to correlate with logs).
    return datetime.now(UTC).strftime("%Y%m%d_%H%M%S_%f")[:-3]


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------


class BrowserTool(Tool):
    name = "browser"
    description = (
        "Drive a headless browser to inspect and test a web page. Actions: open, "
        "snapshot, click, fill, type, press, hover, select, check, wait_for, "
        "screenshot, evaluate, console, network, close. Works with local dev "
        "servers (loopback is allowed by default) and keeps page state between "
        "calls. Every mutating action returns the new page state plus console "
        "errors and failed requests."
    )
    parameters = {  # noqa: RUF012
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "open", "snapshot", "click", "fill", "type", "press",
                    "hover", "select", "check", "wait_for", "screenshot",
                    "evaluate", "console", "network", "close",
                ],
                "description": "Browser action to perform",
            },
            "url": {
                "type": "string",
                "description": "URL for open/wait_for (http:// or https://)",
            },
            "ref": {
                "type": "string",
                "description": "Element ref from the latest snapshot, e.g. e12",
            },
            "selector": {
                "type": "string",
                "description": "CSS/Playwright selector when not using ref",
            },
            "text": {
                "type": "string",
                "description": "Text to fill/type, or text to find for wait_for",
            },
            "key": {
                "type": "string",
                "description": "Key to press, e.g. Enter, Tab, Escape",
            },
            "value": {
                "type": "string",
                "description": "Option value/label for select, or the toggle target for check",
            },
            "script": {
                "type": "string",
                "description": "JavaScript for evaluate or wait_for (page function)",
            },
            "timeout_ms": {
                "type": "integer",
                "description": "Action timeout in milliseconds (default from config)",
            },
            "full_page": {
                "type": "boolean",
                "description": "Capture the full scrollable page (screenshot)",
            },
            "output_path": {
                "type": "string",
                "description": "PNG path inside the workspace (screenshot)",
            },
            "tab": {
                "type": "string",
                "description": "Optional page id from a previous snapshot, e.g. p1",
            },
            "checked": {
                "type": "boolean",
                "description": "Desired state for check (default true)",
            },
            "clear_first": {
                "type": "boolean",
                "description": "Clear the field before typing (type)",
            },
            "state": {
                "type": "string",
                "enum": ["attached", "detached", "visible", "hidden"],
                "description": "Desired element state for wait_for (default visible)",
            },
        },
        "required": ["action"],
        "additionalProperties": False,
    }

    workspace = Path(".")

    def __init__(self, manager: BrowserManager | None = None) -> None:
        self._injected_manager = manager

    @property
    def manager(self) -> BrowserManager:
        return self._injected_manager or get_browser_manager()

    @contextlib.contextmanager
    def active(self, session_id: str | None) -> Iterator[None]:
        """Bind the running turn's session for the duration of a turn."""
        token = _active_session.set(session_id)
        try:
            yield
        finally:
            _active_session.reset(token)

    def _session_id(self) -> str:
        return _active_session.get() or "__default__"

    def _config(self) -> Any:
        cfg = live_config()
        return getattr(cfg, "browser", None)

    async def execute(
        self,
        action: str,
        url: str | None = None,
        ref: str | None = None,
        selector: str | None = None,
        text: str | None = None,
        key: str | None = None,
        value: str | None = None,
        script: str | None = None,
        timeout_ms: int | None = None,
        full_page: bool = False,
        output_path: str | None = None,
        tab: str | None = None,
        checked: bool = True,
        clear_first: bool = False,
        state: str | None = None,
        **_: Any,
    ) -> ToolResult:
        try:
            cfg = self._config()
        except Exception as exc:  # noqa: BLE001
            return tool_error(f"browser configuration unavailable: {exc}", exc)

        if cfg is None:
            return tool_error("browser configuration is missing from Config")
        if not bool(getattr(cfg, "enabled", True)):
            return tool_error("the browser tool is disabled (set [browser] enabled=true)")

        session_id = self._session_id()
        manager = self.manager

        try:
            if action == "close" and not tab:
                await manager.close_session(session_id)
                return ToolResult(output="Browser session closed.")

            session = await manager.session(session_id, cfg)
            async with session.lock:
                session.last_used = time.monotonic()
                result = await self._dispatch(
                    action, session, cfg, url=url, ref=ref, selector=selector,
                    text=text, key=key, value=value, script=script,
                    timeout_ms=timeout_ms, full_page=full_page,
                    output_path=output_path, tab=tab, checked=checked,
                    clear_first=clear_first, state=state,
                )
                session.last_used = time.monotonic()
                return result
        except BrowserUnavailable as exc:
            return tool_error(str(exc), exc)
        except BrowserActionError as exc:
            return tool_error(str(exc), exc)
        except Exception as exc:
            log.exception("browser action '%s' failed", action)
            return tool_error(f"browser action '{action}' failed: {exc}", exc)

    async def _dispatch(
        self,
        action: str,
        session: _SessionState,
        cfg: Any,
        *,
        url: str | None,
        ref: str | None,
        selector: str | None,
        text: str | None,
        key: str | None,
        value: str | None,
        script: str | None,
        timeout_ms: int | None,
        full_page: bool,
        output_path: str | None,
        tab: str | None,
        checked: bool,
        clear_first: bool,
        state: str | None,
    ) -> ToolResult:
        timeout = int(timeout_ms or int(getattr(cfg, "action_timeout_seconds", 15)) * 1000)

        if action == "open":
            return await self._open(session, cfg, url, tab)
        if action == "snapshot":
            return await self._snapshot(session, cfg, tab)
        if action == "console":
            return self._console(session, tab)
        if action == "network":
            return self._network(session, tab)
        if action == "evaluate":
            return await self._evaluate(session, cfg, script, tab)
        if action == "screenshot":
            return await self._screenshot(
                session, cfg, output_path, full_page, tab, ref, selector
            )
        if action == "close":
            return await self._close_tab(session, tab)

        page_state = session.get_page(tab)
        if page_state is None:
            return tool_error(
                f"no open page{' for tab '+tab if tab else ''}; run action='open' first"
            )

        if action == "wait_for":
            return await self._wait_for(
                session, page_state, cfg, timeout, ref, selector, text, url,
                script, state,
            )

        if not ref and not selector:
            return tool_error(f"action '{action}' requires ref or selector from a snapshot")

        cursor = page_state.seq
        try:
            locator = self._locator(page_state, ref, selector)
            if action == "click":
                await locator.click(timeout=timeout)
            elif action == "fill":
                await locator.fill(text or "", timeout=timeout)
            elif action == "type":
                if clear_first:
                    await locator.fill("", timeout=timeout)
                await locator.press_sequentially(text or "", timeout=timeout)
            elif action == "press":
                if not key:
                    return tool_error("action 'press' requires key")
                await locator.press(key, timeout=timeout)
            elif action == "hover":
                await locator.hover(timeout=timeout)
            elif action == "select":
                if value is None:
                    return tool_error("action 'select' requires value")
                try:
                    await locator.select_option(value=value, timeout=timeout)
                except Exception:  # noqa: BLE001
                    await locator.select_option(label=value, timeout=timeout)
            elif action == "check":
                if checked:
                    await locator.check(timeout=timeout)
                else:
                    await locator.uncheck(timeout=timeout)
            else:
                return tool_error(f"unsupported action '{action}'")
        except Exception as exc:  # noqa: BLE001
            return await self._action_failure(
                session, page_state, cfg, action, ref, selector, exc, cursor
            )

        if action == "select":
            try:
                selected = await locator.evaluate(
                    "el => Array.from(el.selectedOptions || []).map(o => o.value)"
                )
            except Exception:  # noqa: BLE001
                selected = value
            extra = f"\nSelected: {selected}"
        elif action in ("fill", "type"):
            try:
                current = await locator.input_value(timeout=timeout)
            except Exception:  # noqa: BLE001
                current = text or ""
            extra = f"\nValue: {current!r}"
        elif action == "check":
            try:
                now_checked = await locator.is_checked()
            except Exception:  # noqa: BLE001
                now_checked = checked
            extra = f"\nChecked: {now_checked}"
        else:
            extra = ""

        snapshot = await self._snapshot_text(
            session, page_state, max_elements=35, max_text=600, include_deltas=False
        )
        _, network_delta = page_state.events_since(cursor)
        blocked = [e for e in network_delta if e.get("kind") == "blocked"]
        label = "blocked" if blocked else "ok"
        return ToolResult(
            output=(
                f"**browser {action}** {label}{extra}\n"
                + self._status_line(page_state)
                + self._deltas(page_state, cursor)
                + "\n" + snapshot
            ),
            error=bool(blocked),
        )

    # -- action implementations ------------------------------------------------

    async def _open(
        self, session: _SessionState, cfg: Any, url: str | None, tab: str | None
    ) -> ToolResult:
        if not url:
            return tool_error("action 'open' requires url")
        reason = validate_browser_url(url, cfg)
        if reason:
            return tool_error(f"URL rejected: {reason}")

        page_state = session.get_page(tab) if tab else None
        if page_state is None and tab:
            return tool_error(f"unknown tab '{tab}'. Omit tab to open a new page.")
        if page_state is None:
            page_state = await self._new_page(session, cfg)
        if page_state is None:
            return tool_error("could not create a browser page")

        cursor = page_state.seq
        nav_timeout = int(getattr(cfg, "navigation_timeout_seconds", 45)) * 1000
        try:
            response = await page_state.page.goto(
                url, wait_until="domcontentloaded", timeout=nav_timeout
            )
            page_state.status = response.status if response is not None else None
            page_state.refs = {}
        except Exception as exc:  # noqa: BLE001
            return await self._action_failure(
                session, page_state, cfg, "open", None, url, exc, cursor
            )

        # Give client-rendered apps a brief moment, but never wait for
        # networkidle: HMR/WebSocket dev servers may never reach it.
        with contextlib.suppress(Exception):
            await page_state.page.wait_for_timeout(250)

        snapshot = await self._snapshot_text(session, page_state, include_deltas=False)
        return ToolResult(
            output=(
                "**browser open** ok\n"
                + self._status_line(page_state)
                + self._deltas(page_state, cursor)
                + "\n" + snapshot
            )
        )

    async def _snapshot(
        self, session: _SessionState, cfg: Any, tab: str | None
    ) -> ToolResult:
        page_state = session.get_page(tab)
        if page_state is None:
            return tool_error("no open page; run action='open' first")
        return ToolResult(output=await self._snapshot_text(session, page_state))

    async def _wait_for(
        self,
        session: _SessionState,
        page_state: _PageState,
        cfg: Any,
        timeout: int,
        ref: str | None,
        selector: str | None,
        text: str | None,
        url: str | None,
        script: str | None,
        state: str | None,
    ) -> ToolResult:
        cursor = page_state.seq
        try:
            if ref or selector:
                locator = self._locator(page_state, ref, selector)
                await locator.wait_for(state=state or "visible", timeout=timeout)
                target = ref or selector
            elif text:
                await page_state.page.get_by_text(text).first.wait_for(
                    state=state or "visible", timeout=timeout
                )
                target = f"text={text!r}"
            elif url:
                await page_state.page.wait_for_url(url, timeout=timeout)
                target = f"url={url!r}"
            elif script:
                await page_state.page.wait_for_function(script, timeout=timeout)
                target = "page function"
            else:
                return tool_error("action 'wait_for' requires selector/ref/text/url/script")
        except Exception as exc:  # noqa: BLE001
            return await self._action_failure(
                session, page_state, cfg, "wait_for", ref, selector, exc, cursor,
                detail=f"target: {ref or selector or text or url or 'script'}",
            )
        return ToolResult(
            output=(
                f"**browser wait_for** ok\nMatched: {target}\n"
                + self._status_line(page_state)
                + self._deltas(page_state, cursor)
            )
        )

    async def _evaluate(
        self,
        session: _SessionState,
        cfg: Any,
        script: str | None,
        tab: str | None,
    ) -> ToolResult:
        page_state = session.get_page(tab)
        if page_state is None:
            return tool_error("no open page; run action='open' first")
        if not script:
            return tool_error("action 'evaluate' requires script")
        cursor = page_state.seq
        try:
            result = await page_state.page.evaluate(script)
        except Exception as exc:  # noqa: BLE001
            return await self._action_failure(
                session, page_state, cfg, "evaluate", None, None, exc, cursor
            )
        try:
            rendered = json.dumps(result, default=str)
        except (TypeError, ValueError):
            rendered = str(result)
        if len(rendered) > 8000:
            rendered = rendered[:8000] + "… (truncated)"
        return ToolResult(
            output=(
                f"**browser evaluate** ok\nURL: {page_state.page.url}\n"
                f"Result: {rendered}" + self._deltas(page_state, cursor)
            )
        )

    async def _screenshot(
        self,
        session: _SessionState,
        cfg: Any,
        output_path: str | None,
        full_page: bool,
        tab: str | None,
        ref: str | None,
        selector: str | None,
    ) -> ToolResult:
        page_state = session.get_page(tab)
        if page_state is None:
            return tool_error("no open page; run action='open' first")
        path = self._resolve_output_path(output_path)
        if isinstance(path, ToolResult):
            return path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if ref or selector:
                await self._locator(page_state, ref, selector).screenshot(path=str(path))
            else:
                await page_state.page.screenshot(
                    path=str(path), full_page=bool(full_page)
                )
        except Exception as exc:  # noqa: BLE001
            return tool_error(f"screenshot failed: {exc}", exc)
        size_kb = path.stat().st_size / 1024
        return ToolResult(
            output=(
                f"**Screenshot saved** ({path.name}, {size_kb:.1f}KB) at {path}\n"
                f"URL: {page_state.page.url}"
            )
        )

    async def _close_tab(self, session: _SessionState, tab: str | None) -> ToolResult:
        page_state = session.get_page(tab)
        if page_state is None:
            return tool_error(f"unknown tab '{tab}'")
        with contextlib.suppress(Exception):
            await page_state.page.close()
        session.remove_page(page_state.id)
        return ToolResult(output=f"Tab {page_state.id} closed.")

    # -- helpers ---------------------------------------------------------------

    async def _new_page(self, session: _SessionState, cfg: Any) -> _PageState | None:
        max_pages = max(1, int(getattr(cfg, "max_pages", 8)))
        if len(session.pages) >= max_pages:
            # Prefer closing the least-recently-used inactive tab. If every tab
            # is active (max_pages=1), reuse the active page instead of failing.
            candidates = [
                ps for ps in session.pages.values() if ps.id != session.active
            ]
            if candidates:
                oldest = min(candidates, key=lambda ps: ps.last_used)
                with contextlib.suppress(Exception):
                    await oldest.page.close()
                session.remove_page(oldest.id)
            else:
                active = session.get_page()
                if active is not None:
                    return active
        try:
            page = await session.context.new_page()
        except Exception as exc:
            raise BrowserActionError(f"could not open a browser page: {exc}") from exc
        page_state = session.add_page(page)
        self._instrument(page_state)
        return page_state

    def _instrument(self, page_state: _PageState) -> None:
        page = page_state.page

        def on_console(message: Any) -> None:
            location = ""
            with contextlib.suppress(Exception):
                loc = message.location
                if loc:
                    location = f"{loc.get('url', '')}:{loc.get('lineNumber', '')}"
            page_state.add_console(message.type, message.text, location)

        def on_page_error(error: Any) -> None:
            page_state.add_console("pageerror", str(error))

        def on_request(request: Any) -> None:
            page_state.pending[request] = {
                "method": request.method,
                "url": request.url,
                "started": time.monotonic(),
            }

        def on_request_finished(request: Any) -> None:
            page_state.pending.pop(request, None)

        def on_request_failed(request: Any) -> None:
            page_state.pending.pop(request, None)
            failure = ""
            with contextlib.suppress(Exception):
                failure = request.failure or ""
            page_state.add_network(
                "request_failed", request.method, request.url, failure=str(failure)
            )

        def on_response(response: Any) -> None:
            with contextlib.suppress(Exception):
                if response.status >= 400:
                    page_state.add_network(
                        "response",
                        response.request.method,
                        response.url,
                        status=response.status,
                    )

        def on_navigated(frame: Any) -> None:
            with contextlib.suppress(Exception):
                if frame == page.main_frame:
                    page_state.refs = {}
                    page_state.pending.clear()

        page.on("console", on_console)
        page.on("pageerror", on_page_error)
        page.on("request", on_request)
        page.on("requestfinished", on_request_finished)
        page.on("requestfailed", on_request_failed)
        page.on("response", on_response)
        page.on("framenavigated", on_navigated)

    def _locator(self, page_state: _PageState, ref: str | None, selector: str | None) -> Any:
        if ref:
            if ref not in page_state.refs:
                raise BrowserActionError(
                    f"Unknown ref '{ref}'. Refs are snapshot-scoped; run "
                    "action='snapshot' and use a current ref."
                )
            selector = f'[data-ca-ref="{ref}"]'
        if not selector:
            raise BrowserActionError("ref or selector is required")
        return page_state.page.locator(selector).first

    def _status_line(self, page_state: _PageState) -> str:
        status = f" (status {page_state.status})" if page_state.status else ""
        return (
            f"Page: {page_state.id}{status}\n"
            f"URL: {page_state.page.url}\n"
            f"Title: {page_state.title}"
        )

    async def _snapshot_text(
        self,
        session: _SessionState,
        page_state: _PageState,
        max_elements: int = 100,
        max_text: int = 2500,
        include_deltas: bool = True,
    ) -> str:
        data = await self._run_snapshot(page_state)
        if data is None:
            rendered = (
                f"Page: {page_state.id}\nURL: {page_state.page.url}\n"
                "(snapshot unavailable: page is navigating or closed)"
            )
            if include_deltas:
                rendered += "\n" + self._deltas(page_state, 0)
            return rendered

        lines = [
            f"Page: {page_state.id}" + (
                f" (status {page_state.status})" if page_state.status else ""
            ),
            f"URL: {data.get('url') or page_state.page.url}",
            f"Title: {page_state.title}",
            "",
            "Interactive elements:",
        ]
        items = data.get("items") or []
        if not items:
            lines.append("(none found)")
        for item in items[:max_elements]:
            flags = []
            if item.get("disabled"):
                flags.append("disabled")
            if item.get("checked"):
                flags.append("checked")
            if item.get("value"):
                flags.append(f"value={item['value']!r}")
            if item.get("href"):
                flags.append(f"href={item['href']!r}")
            suffix = (" " + " ".join(flags)) if flags else ""
            name = item.get("name") or ""
            lines.append(
                f"[{item.get('ref')}] {item.get('role')}"
                + (f" \"{name}\"" if name else "")
                + suffix
            )
        if len(items) > max_elements:
            lines.append(f"… {len(items) - max_elements} more elements not shown")

        text = (data.get("text") or "").strip()
        if text and max_text:
            lines.extend(["", "Visible text:", text[:max_text]])
            if len(text) > max_text:
                lines.append("… (text truncated)")

        rendered = "\n".join(lines)
        if len(rendered) > 8000:
            rendered = rendered[:8000] + "\n… (snapshot truncated)"
        if include_deltas:
            rendered += self._deltas(page_state, 0)
        return rendered

    async def _run_snapshot(self, page_state: _PageState) -> dict | None:
        try:
            data = await page_state.page.evaluate(_SNAPSHOT_SCRIPT)
        except Exception:  # noqa: BLE001
            return None
        if not isinstance(data, dict):
            return None
        page_state.title = str(data.get("title") or "")
        page_state.refs = {
            item["ref"]: f"{item.get('role', '')} {item.get('name', '')}".strip()
            for item in data.get("items", [])
            if item.get("ref")
        }
        return data

    def _deltas(self, page_state: _PageState, cursor: int) -> str:
        console, network = page_state.events_since(cursor)
        errors = [e for e in console if e["level"] in ("error", "pageerror")]
        failed = [
            e for e in network
            if e["kind"] == "request_failed"
            or (e["kind"] == "response" and int(e.get("status", 0)) >= 400)
        ]
        lines = ["", f"Console errors since last action: {len(errors)}"]
        for entry in errors[:8]:
            location = f" ({entry['location']})" if entry.get("location") else ""
            lines.append(f"  [{entry['level']}] {entry['text']}{location}")
        if len(errors) > 8:
            lines.append(f"  … {len(errors) - 8} more")

        lines.append(f"Failed requests since last action: {len(failed)}")
        for entry in failed[:8]:
            status = f" status={entry['status']}" if entry.get("status") else ""
            failure = f" {entry['failure']}" if entry.get("failure") else ""
            lines.append(f"  {entry['method']} {entry['url']}{status}{failure}")
        if len(failed) > 8:
            lines.append(f"  … {len(failed) - 8} more")
        return "\n".join(lines)

    def _console(self, session: _SessionState, tab: str | None) -> ToolResult:
        page_state = session.get_page(tab)
        if page_state is None:
            return tool_error("no open page; run action='open' first")
        entries = list(page_state.console)[-100:]
        lines = [
            (
                f"Console messages: {len(page_state.console)} buffered "
                f"(showing {len(entries)})"
            ),
            f"URL: {page_state.page.url}",
        ]
        for entry in entries:
            location = f" ({entry['location']})" if entry.get("location") else ""
            lines.append(f"[{entry['level']}] {entry['text']}{location}")
        if not entries:
            lines.append("(no console messages)")
        return ToolResult(output="\n".join(lines))

    def _network(self, session: _SessionState, tab: str | None) -> ToolResult:
        page_state = session.get_page(tab)
        if page_state is None:
            return tool_error("no open page; run action='open' first")
        entries = list(page_state.network)[-100:]
        lines = [
            (
                f"Failed/error network events: {len(page_state.network)} buffered "
                f"(showing {len(entries)})"
            ),
            f"Pending requests: {len(page_state.pending)}",
            f"URL: {page_state.page.url}",
        ]
        for entry in entries:
            status = f" status={entry['status']}" if entry.get("status") else ""
            failure = f" {entry['failure']}" if entry.get("failure") else ""
            lines.append(
                f"[{entry['kind']}] {entry['method']} {entry['url']}{status}{failure}"
            )
        for request, meta in list(page_state.pending.items())[:20]:
            elapsed = time.monotonic() - meta.get("started", time.monotonic())
            lines.append(
                f"[pending {elapsed:.1f}s] {meta.get('method')} {meta.get('url')}"
            )
        if not entries and not page_state.pending:
            lines.append("(no failed or pending requests)")
        return ToolResult(output="\n".join(lines))

    async def _action_failure(
        self,
        session: _SessionState,
        page_state: _PageState,
        cfg: Any,
        action: str,
        ref: str | None,
        selector: str | None,
        exc: Exception,
        cursor: int,
        detail: str | None = None,
    ) -> ToolResult:
        target = ref or selector or ""
        message = f"browser {action} failed"
        if target:
            message += f" for {target!r}"
        if detail:
            message += f" ({detail})"
        message += f": {exc}"

        shot = await self._failure_screenshot(page_state, cfg)
        snapshot = await self._snapshot_text(
            session, page_state, max_elements=40, max_text=800, include_deltas=False
        )
        lines = [message, "", self._status_line(page_state)]
        if shot:
            lines.append(f"Failure screenshot: {shot}")
        lines.append(self._deltas(page_state, cursor))
        lines.append("")
        lines.append(snapshot)
        return ToolResult(output="\n".join(lines), error=True)

    async def _failure_screenshot(self, page_state: _PageState, cfg: Any) -> str:
        try:
            base = self.workspace.resolve()
            filename = f"browser_failure_{_timestamp()}.png"
            path = validate_path(str(base / filename), base)
            path.parent.mkdir(parents=True, exist_ok=True)
            await page_state.page.screenshot(path=str(path))
            return str(path)
        except Exception:  # noqa: BLE001
            return ""

    def _resolve_output_path(self, output_path: str | None) -> Path | ToolResult:
        base = self.workspace.resolve()
        if output_path:
            candidate = Path(output_path)
            if not candidate.is_absolute():
                candidate = base / candidate
            try:
                return validate_path(str(candidate), base)
            except WorkspaceViolationError as exc:
                return tool_error(f"output_path must be inside the workspace: {exc}", exc)
        return validate_path(str(base / f"browser_{_timestamp()}.png"), base)
