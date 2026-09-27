"""Tests for pattern-based permission system."""
import pytest

from codeassist.permissions import (
    PermissionManager,
    PermissionRule,
    PermissionRuleset,
    SavedPermissions,
)


class TestPermissionRule:
    def test_wildcard_matches_all(self):
        rule = PermissionRule(tool_name="read", action="allow", pattern="*")
        assert rule.matches_path("/any/path/file.txt")
        assert rule.matches_path("relative/path.py")
        assert rule.matches_path("")

    def test_glob_pattern_match(self):
        rule = PermissionRule(tool_name="write", action="ask", pattern="*.env")
        assert rule.matches_path(".env")
        assert rule.matches_path("/path/to/.env")
        assert rule.matches_path("app.env")
        assert not rule.matches_path("config.py")

    def test_directory_pattern(self):
        rule = PermissionRule(tool_name="write", action="allow", pattern="src/*")
        assert rule.matches_path("src/main.py")
        assert not rule.matches_path("tests/main.py")


class TestPermissionRuleset:
    def test_default_ask(self):
        ruleset = PermissionRuleset()
        assert ruleset.check("unknown_tool") == "ask"

    def test_allow_rule(self):
        ruleset = PermissionRuleset()
        ruleset.add_default("read", "allow")
        assert ruleset.check("read") == "allow"

    def test_deny_rule(self):
        ruleset = PermissionRuleset()
        ruleset.add_default("shell", "deny")
        assert ruleset.check("shell") == "deny"

    def test_specific_pattern_wins(self):
        ruleset = PermissionRuleset()
        ruleset.add_default("write", "ask")
        ruleset.add_rule(PermissionRule(tool_name="write", action="allow", pattern="src/*"))
        assert ruleset.check("write", "src/main.py") == "allow"
        assert ruleset.check("write", "tests/main.py") == "ask"

    def test_deny_takes_precedence(self):
        ruleset = PermissionRuleset()
        ruleset.add_default("write", "allow")
        ruleset.add_rule(PermissionRule(tool_name="write", action="deny", pattern="*.env"))
        assert ruleset.check("write", ".env") == "deny"

    def test_serialization(self):
        ruleset = PermissionRuleset()
        ruleset.add_default("read", "allow")
        ruleset.add_default("write", "ask")
        data = ruleset.to_dict()
        restored = PermissionRuleset.from_dict(data)
        assert restored.check("read") == "allow"
        assert restored.check("write") == "ask"

    def test_legacy_format(self):
        data = {"read": "allow", "write": "ask", "shell": "deny"}
        ruleset = PermissionRuleset.from_dict(data)
        assert ruleset.check("read") == "allow"
        assert ruleset.check("write") == "ask"
        assert ruleset.check("shell") == "deny"

    def test_tools_by_action(self):
        ruleset = PermissionRuleset()
        ruleset.add_default("read", "allow")
        ruleset.add_default("glob", "allow")
        ruleset.add_default("write", "ask")
        ruleset.add_default("shell", "deny")
        assert sorted(ruleset.get_tools_by_action("allow")) == ["glob", "read"]
        assert sorted(ruleset.get_tools_by_action("deny")) == ["shell"]


class TestSavedPermissions:
    def test_check_saved_no_match(self):
        saved = SavedPermissions()
        assert saved.check_saved("read", "/path/file.txt") is None

    def test_check_saved_wildcard(self):
        saved = SavedPermissions()
        saved._cache["read:*"] = "allow"
        assert saved.check_saved("read", "/any/path.txt") == "allow"

    def test_check_saved_filename(self):
        saved = SavedPermissions()
        saved._cache["write:.env"] = "ask"
        assert saved.check_saved("write", "/path/to/.env") == "ask"


class TestPermissionManager:
    @pytest.mark.asyncio
    async def test_initialize(self):
        pm = PermissionManager()
        await pm.initialize({"read": "allow", "write": "ask"})
        assert pm.get_allowed_tools() == ["read"]

    @pytest.mark.asyncio
    async def test_check_with_agent_ruleset(self):
        pm = PermissionManager()
        agent_rs = PermissionRuleset()
        agent_rs.add_default("read", "allow")
        agent_rs.add_default("shell", "deny")
        await pm.initialize()

        action = await pm.check_permission("read", "", agent_rs)
        assert action == "allow"

        action = await pm.check_permission("shell", "", agent_rs)
        assert action == "deny"

    @pytest.mark.asyncio
    async def test_saved_allows_override(self):
        pm = PermissionManager()
        pm.saved._cache["write:*"] = "allow"
        await pm.initialize()
        action = await pm.check_permission("write", "/path/file.txt")
        assert action == "allow"

    @pytest.mark.asyncio
    async def test_config_with_patterns(self):
        pm = PermissionManager()
        config = {
            "write": {
                "*": "ask",
                "*.env": "deny",
                "src/*": "allow",
            },
        }
        await pm.initialize(config)
        assert await pm.check_permission("write", ".env") == "deny"
        assert await pm.check_permission("write", "src/main.py") == "allow"
        assert await pm.check_permission("write", "other/file.txt") == "ask"


class TestAgentDenyIsAuthoritative:
    """An agent's explicit deny must survive global conveniences.

    Regression: a remembered "allow" used to short-circuit check_permission
    before the agent ruleset was consulted, so ticking "remember" on an edit in
    the default agent permanently defeated the review agent's "edit": deny —
    letting a read-only agent write code with no prompt at all.
    """

    @staticmethod
    def _review_ruleset():
        rs = PermissionRuleset()
        rs.add_default("edit", "deny")
        rs.add_default("write", "deny")
        return rs

    @pytest.mark.asyncio
    async def test_remembered_allow_cannot_override_agent_deny(self):
        pm = PermissionManager()
        rs = self._review_ruleset()

        assert await pm.check_permission("edit", "a.py", rs) == "deny"
        # The user approves an edit in the *default* agent and ticks remember.
        pm.saved._cache["edit:*"] = "allow"

        assert await pm.check_permission("edit", "a.py", rs) == "deny"

    @pytest.mark.asyncio
    async def test_remembered_allow_still_applies_without_agent_ruleset(self):
        """Preferring the user's saved choice is still correct when no agent
        ruleset is in play — the fix must not disable remembered permissions."""
        pm = PermissionManager()
        pm.saved._cache["edit:*"] = "allow"
        assert await pm.check_permission("edit", "a.py") == "allow"

    @pytest.mark.asyncio
    async def test_remembered_ask_cannot_override_agent_deny(self):
        pm = PermissionManager()
        pm.saved._cache["write:*"] = "ask"
        assert await pm.check_permission("write", "a.py", self._review_ruleset()) == "deny"

    @pytest.mark.asyncio
    async def test_agent_allow_still_beats_global_deny_default(self):
        """Sanity: a permitted tool is still permitted."""
        pm = PermissionManager()
        rs = PermissionRuleset()
        rs.add_default("read", "allow")
        assert await pm.check_permission("read", "a.py", rs) == "allow"

    @pytest.mark.asyncio
    async def test_unrelated_tool_unaffected_by_agent_deny(self):
        """Scoping the deny to one tool must not deny everything."""
        pm = PermissionManager()
        rs = self._review_ruleset()
        assert await pm.check_permission("read", "a.py", rs) == "ask"

    @pytest.mark.asyncio
    async def test_specific_deny_pattern_respected(self):
        """A path-scoped deny is also authoritative over a remembered wildcard."""
        pm = PermissionManager()
        rs = PermissionRuleset()
        rs.add_rule(PermissionRule(tool_name="edit", action="deny", pattern="*.env"))
        pm.saved._cache["edit:*"] = "allow"

        assert await pm.check_permission("edit", "config.env", rs) == "deny"
        assert await pm.check_permission("edit", "main.py", rs) == "allow"


class TestAgentPermissionsToRuleset:
    """AgentPermissions must produce an enforceable ruleset."""

    def test_round_trips_actions(self):
        from codeassist.agents import AgentPermissions, Permission

        perms = AgentPermissions([
            Permission("read", "allow"),
            Permission("write", "deny"),
            Permission("shell", "confirm"),
        ])
        rs = perms.to_ruleset()
        assert rs.check("read") == "allow"
        assert rs.check("write") == "deny"
        assert rs.check("shell") == "confirm"

    def test_unlisted_tool_still_asks(self):
        from codeassist.agents import AgentPermissions, Permission

        rs = AgentPermissions([Permission("read", "allow")]).to_ruleset()
        assert rs.check("apply_patch") == "ask"
