import ipaddress
import logging
import socket
import threading
import typing
from pathlib import Path
from urllib.parse import urlparse

import httpx
from httpcore._backends.base import SOCKET_OPTION

log = logging.getLogger(__name__)

# Internal/reserved TLDs and suffixes that should be blocked
_BLOCKED_SUFFIXES = (
    ".internal", ".local", ".localdomain", ".corp", ".home", ".lan",
    ".private", ".test", ".localhost",
)

# Cloud metadata IPs (IPv4 and IPv6)
_BLOCKED_LINK_LOCAL_IPS = {
    "169.254.169.254",  # AWS/GCP/Azure metadata
    "fd00:ec2::254",    # AWS IPv6 metadata
}

# Well-known non-HTTP internal service ports. A validated host on any of these is
# treated as an SSRF vector into local admin/database/remote-access services even
# when the host itself resolves to a public IP -- the concern is the *service*,
# not the address. HTTP(S) (80/443) and other web ports are intentionally left
# open so legitimate fetches are not broken.
_BLOCKED_SERVICE_PORTS = frozenset({
    20, 21,     # FTP / FTP-data
    22,         # SSH
    23,         # Telnet
    25,         # SMTP
    110,        # POP3
    135, 139,   # MSRPC / NetBIOS
    445,        # SMB
    1433, 1434, # MSSQL
    1521,       # Oracle
    3306,       # MySQL
    3389,       # RDP
    5432,       # PostgreSQL
    5672,       # AMQP
    6379,       # Redis
    9042, 9200, 9300,  # Cassandra / Elasticsearch
    11211,      # Memcached
    27017,      # MongoDB
})


class WorkspaceViolationError(Exception):
    """Raised when a path escapes the workspace boundary."""


def validate_path(file_path: str, workspace: Path) -> Path:
    """Resolve and validate that a path is within the workspace.

    Returns the resolved Path if valid. Raises WorkspaceViolationError otherwise.
    """
    try:
        resolved = Path(file_path).resolve()
    except (TypeError, OSError):
        raise WorkspaceViolationError(f"Invalid path: {file_path}")

    try:
        workspace_resolved = workspace.resolve()
    except OSError:
        raise WorkspaceViolationError(
            f"Could not resolve workspace path: {workspace}. "
            f"Ensure the workspace directory exists."
        )

    try:
        resolved.relative_to(workspace_resolved)
    except ValueError:
        raise WorkspaceViolationError(
            f"Path '{file_path}' is outside the workspace '{workspace}'. "
            f"Resolved to '{resolved}'."
        )

    return resolved


def validate_directory(dir_path: str, workspace: Path) -> Path:
    """Validate a directory path is within the workspace."""
    try:
        resolved = Path(dir_path).resolve()
    except (TypeError, OSError):
        raise WorkspaceViolationError(f"Invalid directory path: {dir_path}")

    try:
        workspace_resolved = workspace.resolve()
    except OSError:
        raise WorkspaceViolationError(
            f"Could not resolve workspace path: {workspace}. "
            f"Ensure the workspace directory exists."
        )

    try:
        resolved.relative_to(workspace_resolved)
    except ValueError:
        raise WorkspaceViolationError(
            f"Directory '{dir_path}' is outside the workspace '{workspace}'."
        )

    if not resolved.is_dir():
        raise WorkspaceViolationError(f"Directory does not exist: {resolved}")

    return resolved


def _is_blocked_ip(ip_str: str) -> bool:
    """Check if an IP address string is in a blocked range."""
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False

    if ip.is_loopback or ip.is_link_local or ip.is_reserved:
        return True

    # Private ranges: 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, fc00::/7
    if ip.is_private:
        return True

    return ip_str in _BLOCKED_LINK_LOCAL_IPS


def _resolve_and_check(hostname: str) -> tuple[bool, list[str]]:
    """Resolve a hostname and check all resulting IPs.

    Returns ``(safe, ips)`` where ``ips`` are the addresses that resolved for
    ``hostname`` -- captured so they can be pinned to the connection (see below).
    """
    try:
        results = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except (socket.gaierror, OSError):
        # DNS resolution failed — block to be safe
        return False, []

    ips = [sockaddr[0] for family, _, _, _, sockaddr in results]
    for ip_str in ips:
        if _is_blocked_ip(ip_str):
            return False, ips

    return True, ips


def validate_url(url: str) -> bool:
    """Check if a URL is safe to fetch (blocks SSRF to private/internal networks).

    Resolves DNS for domain names and checks all resulting IP addresses against
    private/reserved ranges. This prevents DNS rebinding attacks where a domain
    resolves to an internal IP at request time. As a side effect it also pins the
    validated IPs so the connection can be forced to them (see ``_PinnedDNSBackend``).

    Returns True if the URL is safe, False if it should be blocked.
    """
    try:
        parsed = urlparse(url)
    except Exception:  # noqa: BLE001
        return False

    if parsed.scheme not in ("http", "https"):
        return False

    # Reject well-known internal service ports up front. Accessing .port raises
    # ValueError on an out-of-range port, which we fail closed on.
    try:
        port = parsed.port
    except ValueError:
        return False
    if port is not None and port in _BLOCKED_SERVICE_PORTS:
        return False

    hostname = parsed.hostname
    if not hostname:
        return False

    hostname_lower = hostname.lower()

    # Block common internal hostnames by name
    blocked_hosts = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "metadata.google.internal"}
    if hostname_lower in blocked_hosts:
        return False

    # Block internal TLDs/suffixes
    for suffix in _BLOCKED_SUFFIXES:
        if hostname_lower.endswith(suffix):
            return False

    # If hostname is a literal IP, check it directly (no DNS needed)
    try:
        ip = ipaddress.ip_address(hostname)
        safe, ips = _resolve_and_check(hostname)
        if ips and safe:
            _pin_ips(hostname, ips)
        return safe
    except ValueError:
        pass  # Not an IP literal — resolve DNS and check

    # Resolve DNS and check all resulting IPs
    safe, ips = _resolve_and_check(hostname)
    if ips and safe:
        _pin_ips(hostname, ips)
    return safe


def _pin_ips(hostname: str, ips: list[str]) -> None:
    """Pin the validated IPs for ``hostname`` so connect-time DNS can't swap them.

    The pool's connect-time lookup could otherwise resolve to a different (rebind)
    IP after validation. Storing the checked addresses lets the pinned-DNS backend
    force the connection to them, closing the TOCTOU window. Thread-local because
    the shared client runs in one event loop and the backend is per-request.
    """
    _PINNED_IPS.__dict__.setdefault(hostname, ips)


def _unpin_ips(hostname: str) -> None:
    _PINNED_IPS.__dict__.pop(hostname, None)


# Thread-local store of validated IPs keyed by hostname (see _pin_ips).
_PINNED_IPS = threading.local()


class _PinnedDNSBackend:
    """Network backend that pins validated IPs at connect time.

    Closes the DNS-rebinding TOCTOU window: ``validate_url`` resolves and checks
    IPs once, then the pool's connect-time lookup could resolve to a different
    (rebind) IP. This backend forces connections to the pre-validated addresses by
    passing them as IP literals to ``anyio.connect_tcp`` (which skips DNS), so a
    rebind at connect time is defeated. TLS SNI still uses the hostname, so HTTPS
    continues to work against the pinned address.

    Delegates everything else (unix sockets, sleep) to httpcore's default backend.
    """

    def __init__(self):
        import httpcore._backends.auto as _auto

        self._default = _auto.AutoBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: typing.Iterable[SOCKET_OPTION] | None = None,
    ):
        pinned = _PINNED_IPS.__dict__.get(host)
        if pinned:
            last_exc: Exception | None = None
            for ip in pinned:
                try:
                    return await self._connect_to_ip(
                        ip, port, timeout=timeout, local_address=local_address,
                        socket_options=socket_options,
                    )
                except Exception as e:  # noqa: BLE001
                    last_exc = e
            log.warning("pinned DNS: all validated IPs for %s failed: %s", host, last_exc)
        # Fail-open residual window (documented per F1): if every pinned IP is
        # unreachable, we fall back to default DNS. An attacker who can make all
        # pinned IPs fail could force free-DNS resolution -- but validation pins
        # during the attacker-influenced step, so this window is narrow.
        return await self._default.connect_tcp(
            host, port, timeout=timeout, local_address=local_address,
            socket_options=socket_options,
        )

    async def _connect_to_ip(
        self, ip: str, port: int, timeout: float | None,
        local_address: str | None, socket_options: typing.Iterable[SOCKET_OPTION] | None,
    ):
        import anyio
        import httpcore
        from httpcore._exceptions import map_exceptions

        exc_map = {
            TimeoutError: httpcore.ConnectTimeout,
            OSError: httpcore.ConnectError,
            anyio.BrokenResourceError: httpcore.ConnectError,
        }
        with map_exceptions(exc_map):
            with anyio.fail_after(timeout):
                stream = await anyio.connect_tcp(
                    remote_host=ip, remote_port=port, local_host=local_address,
                )
                for option in (socket_options or []):
                    stream._raw_socket.setsockopt(*option)
        return httpcore.backends.anyio.AnyIOStream(stream)

    async def connect_unix_socket(self, path: str, timeout: float | None = None, socket_options=None):
        return await self._default.connect_unix_socket(path, timeout=timeout, socket_options=socket_options)

    async def sleep(self, seconds: float) -> None:
        return await self._default.sleep(seconds)


class _PinnedHTTPTransport(httpx._transports.default.AsyncHTTPTransport):
    """Custom httpx transport that pins validated IPs at connect time.

    httpx's default ``AsyncHTTPTransport`` builds its pool with httpcore's default
    network backend (plain DNS at connect time) -- the TOCTOU window. This subclass
    swaps in a pinned pool (``_PinnedDNSBackend``) right after the parent creates it,
    so connections are forced to the pre-validated addresses and closing the rebinding
    window for the shared fetch client. Handles plain, HTTP-proxy, and SOCKS pools.
    """

    def __init__(self, **kwargs):
        import httpcore

        # Let the parent set up proxy/SSL handling and create its pool...
        super().__init__(**kwargs)

        network_backend = _PinnedDNSBackend()
        pool = self._pool

        if isinstance(pool, httpcore.AsyncConnectionPool):
            # Plain (non-proxy) pool: inject our pinned DNS backend.
            self._pool = httpcore.AsyncConnectionPool(
                ssl_context=kwargs.get("ssl_context"),
                max_connections=pool._max_connections,
                max_keepalive_connections=pool._max_keepalive_connections,
                keepalive_expiry=pool._keepalive_expiry,
                http1=pool._http1,
                http2=pool._http2,
                network_backend=network_backend,
            )
        elif isinstance(pool, (httpcore.AsyncHTTPProxy, httpcore.AsyncSOCKSProxy)):
            # Proxy pools reuse the same network backend for their hops.
            pool.network_backend = network_backend
