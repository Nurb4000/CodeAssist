"""Pattern-based permission system for agents.

Supports granular permissions per tool with glob patterns for file paths.
Actions: "allow", "deny", "ask" (confirmation required).
Saved permission preferences persist across sessions.
"""

import fnmatch
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .session import get_db

log = logging.getLogger(__name__)


@dataclass
class PermissionRule:
    """A single permission rule for a tool."""
    tool_name: str
    action: str  # "allow", "deny", "ask"
    pattern: str = "*"  # Glob pattern for file paths
    description: str = ""

    def matches_path(self, file_path: str) -> bool:
        """Check if a file path matches this rule's pattern."""
        if self.pattern == "*":
            return True
        return fnmatch.fnmatch(file_path, self.pattern) or fnmatch.fnmatch(Path(file_path).name, self.pattern)


class PermissionRuleset:
    """Collection of permission rules for an agent.

    Rules are evaluated in order: deny > ask > allow.
    More specific patterns take precedence over wildcards.
    """

    def __init__(self):
        self._rules: dict[str, list[PermissionRule]] = {}  # tool_name -> [rules]

    def add_rule(self, rule: PermissionRule):
        """Add a permission rule."""
        if rule.tool_name not in self._rules:
            self._rules[rule.tool_name] = []
        self._rules[rule.tool_name].append(rule)

    def add_default(self, tool_name: str, action: str, pattern: str = "*"):
        """Add a default rule for a tool."""
        self.add_rule(PermissionRule(tool_name=tool_name, action=action, pattern=pattern))

    def check(self, tool_name: str, file_path: str = "") -> str:
        """Check permission for a tool call. Returns 'allow', 'deny', or 'ask'."""
        rules = self._rules.get(tool_name, [])
        if not rules:
            return "ask"  # Default to asking if no rule exists

        # Find matching rules, prefer most specific
        best_match = None
        best_specificity = -1

        for rule in rules:
            if rule.matches_path(file_path):
                # Specificity: longer pattern = more specific
                specificity = len(rule.pattern) if rule.pattern != "*" else 0
                if specificity > best_specificity:
                    best_specificity = specificity
                    best_match = rule

        if best_match:
            return best_match.action

        # No matching rule — check for wildcard
        for rule in rules:
            if rule.pattern == "*":
                return rule.action

        return "ask"  # Default

    def get_tools_by_action(self, action: str) -> list[str]:
        """Get list of tool names that have a given default action."""
        result = []
        for tool_name, rules in self._rules.items():
            for rule in rules:
                if rule.action == action and rule.pattern == "*":
                    result.append(tool_name)
                    break
        return result

    def to_dict(self) -> dict:
        """Serialize ruleset to dict."""
        return {
            tool_name: [
                {
                    "action": r.action,
                    "pattern": r.pattern,
                    "description": r.description,
                }
                for r in rules
            ]
            for tool_name, rules in self._rules.items()
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PermissionRuleset":
        """Deserialize ruleset from dict."""
        ruleset = cls()
        for tool_name, rule_list in data.items():
            if isinstance(rule_list, list):
                for r in rule_list:
                    if isinstance(r, dict):
                        ruleset.add_rule(PermissionRule(
                            tool_name=tool_name,
                            action=r.get("action", "ask"),
                            pattern=r.get("pattern", "*"),
                            description=r.get("description", ""),
                        ))
            elif isinstance(rule_list, str):
                # Legacy format: { "tool_name": "action" }
                ruleset.add_default(tool_name, rule_list)
        return ruleset


class SavedPermissions:
    """Manages persisted permission preferences."""

    def __init__(self):
        self._cache: dict[str, str] = {}  # "tool:pattern" -> action

    async def load(self) -> None:
        """Load saved permissions from database."""
        try:
            db = await get_db()
            async with db:
                async with db.execute(
                    "SELECT tool_name, pattern, action FROM permission_saves"
                ) as cursor:
                    rows = await cursor.fetchall()
                    for tool_name, pattern, action in rows:
                        key = f"{tool_name}:{pattern}"
                        self._cache[key] = action
        except Exception as e:
            log.debug("Failed to load saved permissions: %s", e)

    async def save(self, tool_name: str, pattern: str, action: str) -> None:
        """Save a permission preference to database."""
        key = f"{tool_name}:{pattern}"
        self._cache[key] = action
        try:
            import uuid
            db = await get_db()
            async with db:
                await db.execute(
                    "INSERT OR REPLACE INTO permission_saves (id, tool_name, pattern, action, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    [str(uuid.uuid4()), tool_name, pattern, action, _now_iso()],
                )
        except Exception as e:
            log.debug("Failed to save permission: %s", e)

    def check_saved(self, tool_name: str, file_path: str = "") -> str | None:
        """Check if there's a saved permission for this tool/path combo."""
        # Check exact pattern match
        key = f"{tool_name}:{file_path}"
        if key in self._cache:
            return self._cache[key]

        # Check wildcard
        key = f"{tool_name}:*"
        if key in self._cache:
            return self._cache[key]

        # Check filename pattern
        if file_path:
            name = Path(file_path).name
            key = f"{tool_name}:{name}"
            if key in self._cache:
                return self._cache[key]

        return None

    async def clear(self) -> None:
        """Clear all saved permissions."""
        self._cache.clear()
        try:
            db = await get_db()
            async with db:
                await db.execute("DELETE FROM permission_saves")
        except Exception as e:
            log.debug("Failed to clear saved permissions: %s", e)


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


class PermissionManager:
    """High-level permission manager combining rulesets and saved preferences."""

    def __init__(self):
        self.ruleset = PermissionRuleset()
        self.saved = SavedPermissions()

    async def initialize(self, config_permissions: dict[str, Any] | None = None):
        """Initialize with default rules and load saved preferences."""
        # Load saved permissions from DB
        await self.saved.load()

        # Apply config overrides
        if config_permissions:
            self._apply_config(config_permissions)

    def _apply_config(self, config: dict[str, Any]):
        """Apply permission configuration from config file."""
        # Format: { "tool_name": { "pattern": "action", ... }, ... }
        # or legacy: { "tool_name": "action", ... }
        for tool_name, value in config.items():
            if isinstance(value, dict):
                for pattern, action in value.items():
                    self.ruleset.add_rule(PermissionRule(
                        tool_name=tool_name,
                        action=str(action),
                        pattern=pattern,
                    ))
            elif isinstance(value, str):
                self.ruleset.add_default(tool_name, value)

    async def check_permission(
        self,
        tool_name: str,
        file_path: str = "",
        agent_ruleset: PermissionRuleset | None = None,
    ) -> str:
        """Check if a tool call is allowed. Returns 'allow', 'deny', or 'ask'."""
        # 1. Check saved permissions first (user's explicit choice)
        saved_action = self.saved.check_saved(tool_name, file_path)
        if saved_action and saved_action == "allow":
            return "allow"

        # 2. Check agent-specific ruleset (if provided)
        if agent_ruleset:
            agent_action = agent_ruleset.check(tool_name, file_path)
            if agent_action == "deny":
                return "deny"
            if agent_action == "allow" and saved_action is None:
                return "allow"

        # 3. Check global ruleset
        global_action = self.ruleset.check(tool_name, file_path)

        # 4. If saved action exists and differs, prefer saved (unless deny)
        if saved_action and saved_action != "deny":
            return saved_action

        return global_action

    async def save_permission_choice(
        self,
        tool_name: str,
        file_path: str,
        action: str,
    ) -> None:
        """Save a user's permission choice for future reference."""
        # Use filename pattern for broader matching
        pattern = Path(file_path).name if file_path else "*"
        await self.saved.save(tool_name, pattern, action)

    def get_allowed_tools(self) -> list[str]:
        """Get list of tools that are allowed by default."""
        return self.ruleset.get_tools_by_action("allow")

    def get_denied_tools(self) -> list[str]:
        """Get list of tools that are denied by default."""
        return self.ruleset.get_tools_by_action("deny")


# Module-level singleton
permission_manager = PermissionManager()
