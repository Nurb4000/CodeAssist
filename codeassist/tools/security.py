import ipaddress
import logging
import socket
from pathlib import Path
from urllib.parse import urlparse

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


def _resolve_and_check(hostname: str) -> bool:
    """Resolve a hostname and check all resulting IPs. Returns True if safe."""
    try:
        results = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except (socket.gaierror, OSError):
        # DNS resolution failed — block to be safe
        return False

    for family, _, _, _, sockaddr in results:
        ip_str = sockaddr[0]
        if _is_blocked_ip(ip_str):
            return False

    return True


def validate_url(url: str) -> bool:
    """Check if a URL is safe to fetch (blocks SSRF to private/internal networks).

    Resolves DNS for domain names and checks all resulting IP addresses against
    private/reserved ranges. This prevents DNS rebinding attacks where a domain
    resolves to an internal IP at request time.

    Returns True if the URL is safe, False if it should be blocked.
    """
    try:
        parsed = urlparse(url)
    except Exception:  # noqa: BLE001
        return False

    if parsed.scheme not in ("http", "https"):
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
        return not _is_blocked_ip(str(ip))
    except ValueError:
        pass  # Not an IP literal — resolve DNS and check

    # Resolve DNS and check all resulting IPs
    return _resolve_and_check(hostname)
