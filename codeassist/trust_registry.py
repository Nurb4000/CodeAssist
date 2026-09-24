"""Trust registry for managing tool/plugin security.

Provides hash-based whitelisting with user approval flow for dynamically
loaded code (plugins, custom tools, dynamic tools).
"""

import hashlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

log = logging.getLogger(__name__)


class TrustStatus(Enum):
    """Trust status for a file."""
    TRUSTED = "trusted"
    UNTRUSTED = "untrusted"
    PENDING_APPROVAL = "pending_approval"


@dataclass
class TrustRequest:
    """Represents a pending trust approval request."""
    file_path: str
    hash: str
    file_type: str  # "plugin", "custom_tool", "dynamic_tool"
    timestamp: float = field(default_factory=lambda: __import__('time').time())
    
    def to_dict(self) -> dict:
        return {
            "file_path": self.file_path,
            "hash": self.hash,
            "file_type": self.file_type,
            "timestamp": self.timestamp,
        }


class TrustRegistry:
    """Manages trust relationships for dynamically loaded code.
    
    Uses SHA-256 hashes to track file integrity and requires explicit
    user approval for new or modified files.
    """
    
    def __init__(self, registry_path: Path | None = None):
        """Initialize trust registry.
        
        Args:
            registry_path: Path to store the trust registry JSON file.
                          Defaults to ~/.codeassist/trusted_tools.json
        """
        if registry_path is None:
            home = Path.home()
            registry_path = home / ".codeassist" / "trusted_tools.json"
        
        self.registry_path = registry_path
        self._registry: dict[str, dict] = {}  # file_path -> {"hash": ..., "approved": bool}
        self._pending_requests: dict[str, TrustRequest] = {}  # file_path -> TrustRequest
        self._approval_callback: Callable[[TrustRequest, bool], None] | None = None
        
        self._load_registry()
    
    def set_approval_callback(self, callback: Callable[[TrustRequest, bool], None]):
        """Set callback for handling approval decisions.
        
        Args:
            callback: Function that receives (trust_request, approved) tuple
        """
        self._approval_callback = callback
    
    def _load_registry(self):
        """Load trust registry from disk."""
        if self.registry_path.exists():
            try:
                with open(self.registry_path, "r", encoding="utf-8") as f:
                    self._registry = json.load(f)
                log.debug("Loaded trust registry from %s", self.registry_path)
            except Exception as e:  # noqa: BLE001
                log.error("Failed to load trust registry: %s", e)
                self._registry = {}
    
    def _save_registry(self):
        """Save trust registry to disk."""
        try:
            self.registry_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.registry_path, "w", encoding="utf-8") as f:
                json.dump(self._registry, f, indent=2)
            log.debug("Saved trust registry to %s", self.registry_path)
        except Exception as e:  # noqa: BLE001
            log.error("Failed to save trust registry: %s", e)
    
    @staticmethod
    def compute_hash(file_path: Path) -> str:
        """Compute SHA-256 hash of a file.
        
        Args:
            file_path: Path to the file
            
        Returns:
            Hex digest of SHA-256 hash
        """
        hasher = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                hasher.update(chunk)
        return hasher.hexdigest()
    
    def check_trust(self, file_path: Path, file_type: str = "tool") -> TrustStatus:
        """Check if a file is trusted.
        
        Args:
            file_path: Path to the file to check
            file_type: Type of file ("plugin", "custom_tool", "dynamic_tool")
            
        Returns:
            TrustStatus indicating trust level
        """
        str_path = str(file_path.resolve())
        
        # Check if file exists
        if not file_path.exists():
            log.warning("File does not exist: %s", file_path)
            return TrustStatus.UNTRUSTED
        
        # Compute current hash
        current_hash = self.compute_hash(file_path)
        
        # Check registry
        if str_path in self._registry:
            registered = self._registry[str_path]
            
            # Check if hash matches
            if registered["hash"] == current_hash:
                if registered.get("approved", False):
                    return TrustStatus.TRUSTED
                else:
                    return TrustStatus.UNTRUSTED
        
        # File not in registry or hash changed - request approval
        request = TrustRequest(
            file_path=str_path,
            hash=current_hash,
            file_type=file_type,
        )
        
        self._pending_requests[str_path] = request
        
        # Notify about pending approval
        if self._approval_callback:
            self._approval_callback(request, False)
        
        log.info("Trust approval requested for %s (%s)", file_path, file_type)
        return TrustStatus.PENDING_APPROVAL
    
    def approve(self, file_path: Path):
        """Approve a file for execution.
        
        Args:
            file_path: Path to the file to approve
        """
        str_path = str(file_path.resolve())
        
        if not file_path.exists():
            log.error("Cannot approve non-existent file: %s", file_path)
            return
        
        current_hash = self.compute_hash(file_path)
        
        self._registry[str_path] = {
            "hash": current_hash,
            "approved": True,
            "approved_at": __import__('time').time(),
        }
        
        # Remove from pending
        if str_path in self._pending_requests:
            del self._pending_requests[str_path]
        
        self._save_registry()
        log.info("Approved file: %s", file_path)
    
    def reject(self, file_path: Path):
        """Reject a file from execution.
        
        Args:
            file_path: Path to the file to reject
        """
        str_path = str(file_path.resolve())
        
        # Add to registry as untrusted if not already there
        if str_path not in self._registry:
            if file_path.exists():
                current_hash = self.compute_hash(file_path)
            else:
                current_hash = ""
            
            self._registry[str_path] = {
                "hash": current_hash,
                "approved": False,
                "rejected_at": __import__('time').time(),
            }
        else:
            self._registry[str_path]["approved"] = False
        
        # Remove from pending
        if str_path in self._pending_requests:
            del self._pending_requests[str_path]
        
        self._save_registry()
        log.info("Rejected file: %s", file_path)
    
    def is_trusted(self, file_path: Path) -> bool:
        """Check if a file is trusted (shorthand for check_trust == TRUSTED).
        
        Args:
            file_path: Path to the file
            
        Returns:
            True if file is trusted
        """
        return self.check_trust(file_path) == TrustStatus.TRUSTED
    
    def get_pending_requests(self) -> list[TrustRequest]:
        """Get all pending trust approval requests.
        
        Returns:
            List of pending TrustRequest objects
        """
        return list(self._pending_requests.values())
    
    def clear_pending(self, file_path: Path):
        """Clear pending request for a file.
        
        Args:
            file_path: Path to the file
        """
        str_path = str(file_path.resolve())
        if str_path in self._pending_requests:
            del self._pending_requests[str_path]
    
    def list_trusted(self) -> list[dict]:
        """List all trusted files.
        
        Returns:
            List of dicts with file info
        """
        result = []
        for path, info in self._registry.items():
            if info.get("approved", False):
                result.append({
                    "path": path,
                    "hash": info["hash"],
                    "approved_at": info.get("approved_at"),
                })
        return result
