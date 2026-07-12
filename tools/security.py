import ipaddress
import logging
from pathlib import Path
from urllib.parse import urlparse

log = logging.getLogger(__name__)


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


def validate_url(url: str) -> bool:
    """Check if a URL is safe to fetch (blocks SSRF to private/internal networks).

    Returns True if the URL is safe, False if it should be blocked.
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return False

    hostname = parsed.hostname
    if not hostname:
        return False

    # Block localhost and common internal hostnames
    blocked_hosts = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "metadata.google.internal"}
    if hostname.lower() in blocked_hosts:
        return False

    # Block private/reserved IP ranges
    try:
        ip = ipaddress.ip_address(hostname)
        if ip.is_private or ip.is_reserved or ip.is_loopback or ip.is_link_local:
            return False
        # Block cloud metadata endpoint (169.254.169.254)
        if str(ip) == "169.254.169.254":
            return False
    except ValueError:
        # hostname is not an IP — that's fine, it's a domain name
        pass

    return True
