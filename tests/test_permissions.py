"""Tests for pattern-based permission system."""
import pytest

from codeassist.permissions import (
    PermissionManager,
    PermissionRule,
    PermissionRuleset,
    SavedPermissions,
)


def _async_recorder(saved: SavedPermissions):
    """Replace ``SavedPermissions.save`` with a spy that still updates the cache.

    Keeps the in-memory cache consistent (so ``check_saved`` reflects the write)
    while recording the exact ``(tool, pattern, action)`` tuple the manager
    passed, without touching the database.
    """

    async def _save(tool_name: str, pattern: str, action: str) -> None:
        saved.saved_calls.append((tool_name, pattern, action))
        saved._cache[f"{tool_name}:{pattern}"] = action

    saved.saved_calls = []
    return _save


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

    def test_valid_actions_are_accepted(self):
        """Every documented action (including 'confirm', which enters via
        per-agent config) must construct without raising."""
        for action in ("allow", "deny", "ask", "confirm"):
            PermissionRule(tool_name="read", action=action)

    def test_invalid_action_raises(self):
        """An unknown action is a malformed rule and must fail loudly rather
        than silently no-op (it would otherwise fall back to 'ask')."""
        with pytest.raises(ValueError, match="Invalid permission action"):
            PermissionRule(tool_name="read", action="permit")


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

    def test_check_saved_exact_path(self):
        saved = SavedPermissions()
        saved._cache["write:/path/to/.env"] = "ask"
        assert saved.check_saved("write", "/path/to/.env") == "ask"
        # A different directory is a different file.
        assert saved.check_saved("write", "/other/.env") is None

    def test_check_saved_glob_pattern(self):
        saved = SavedPermissions()
        saved._cache["write:/secrets/*"] = "deny"
        assert saved.check_saved("write", "/secrets/prod.env") == "deny"
        assert saved.check_saved("write", "/public/prod.env") is None

    def test_bare_filename_does_not_match_everywhere(self):
        """Regression: remembering one file must not allow same-named files.

        Grants used to be stored as ``Path(file_path).name`` and matched by
        basename, so approving ``/secrets/prod.env`` silently allowed
        ``/anywhere/prod.env``.
        """
        saved = SavedPermissions()
        saved._cache["write:prod.env"] = "allow"
        assert saved.check_saved("write", "/secrets/prod.env") is None
        assert saved.check_saved("write", "/tmp/prod.env") is None


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

    @pytest.mark.asyncio
    async def test_initialize_rejects_invalid_action(self):
        """A malformed action in the permission config must raise at load, not
        silently become a no-op rule (§13 #14)."""
        pm = PermissionManager()
        config = {"write": {"*": "maybe"}}
        with pytest.raises(ValueError, match="Invalid permission action"):
            await pm.initialize(config)

    @pytest.mark.asyncio
    async def test_saved_deny_is_authoritative(self):
        """A saved "deny" (never ask again) must not fall through to ask/allow."""
        pm = PermissionManager()
        pm.saved._cache["write:*"] = "deny"
        assert await pm.check_permission("write", "/any/file.txt") == "deny"

    @pytest.mark.asyncio
    async def test_saved_deny_beats_agent_allow(self):
        pm = PermissionManager()
        rs = PermissionRuleset()
        rs.add_default("write", "allow")
        pm.saved._cache["write:*"] = "deny"
        assert await pm.check_permission("write", "a.py", rs) == "deny"

    @pytest.mark.asyncio
    async def test_saved_ask_beats_agent_allow(self):
        """A user's explicit "ask" still prompts even if the agent allows it."""
        pm = PermissionManager()
        rs = PermissionRuleset()
        rs.add_default("write", "allow")
        pm.saved._cache["write:*"] = "ask"
        assert await pm.check_permission("write", "a.py", rs) == "ask"

    @pytest.mark.asyncio
    async def test_save_permission_choice_stores_full_path(self):
        """Remembering a file must key on the exact path, never the basename."""
        pm = PermissionManager()
        pm.saved.save = _async_recorder(pm.saved)
        await pm.save_permission_choice("write", "/secrets/prod.env", "allow")
        assert pm.saved.saved_calls == [("write", "/secrets/prod.env", "allow")]
        # The grant applies to that path only.
        assert pm.saved.check_saved("write", "/secrets/prod.env") == "allow"
        assert pm.saved.check_saved("write", "/elsewhere/prod.env") is None

    @pytest.mark.asyncio
    async def test_save_permission_choice_pathless_is_wildcard(self):
        pm = PermissionManager()
        pm.saved.save = _async_recorder(pm.saved)
        await pm.save_permission_choice("git", "", "allow")
        assert pm.saved.saved_calls == [("git", "*", "allow")]
        assert pm.saved.check_saved("git") == "allow"


class TestSavedPermissionsPersistence:
    """Saved preferences must survive a restart and be clearable.

    Regression: ``PermissionManager.initialize()`` (the only caller of
    ``SavedPermissions.load()``) was never invoked at startup, so grants written
    with "remember" were invisible after a restart. ``clear()`` also awaited
    ``get_db()`` directly (it is an async context manager), raising TypeError so
    the DELETE never ran.
    """

    @pytest.mark.asyncio
    async def test_save_then_load_round_trips(self):
        from codeassist.session import init_db

        await init_db()
        writer = SavedPermissions()
        await writer.save("write", "/secrets/prod.env", "deny")
        await writer.save("read", "*", "allow")

        # A fresh instance (== process restart) sees the same decisions.
        reader = SavedPermissions()
        await reader.load()
        assert reader.check_saved("write", "/secrets/prod.env") == "deny"
        assert reader.check_saved("read", "/any/where.py") == "allow"

    @pytest.mark.asyncio
    async def test_clear_removes_persisted_rows(self):
        from codeassist.session import init_db

        await init_db()
        saved = SavedPermissions()
        await saved.save("write", "/secrets/prod.env", "deny")

        await saved.clear()
        assert saved._cache == {}

        reloaded = SavedPermissions()
        await reloaded.load()
        assert reloaded.check_saved("write", "/secrets/prod.env") is None

    @pytest.mark.asyncio
    async def test_initialize_loads_persisted_permissions(self):
        """The startup path (initialize) must populate the saved cache."""
        from codeassist.session import init_db

        await init_db()
        await SavedPermissions().save("edit", "/workspace/main.py", "allow")

        pm = PermissionManager()
        await pm.initialize()
        assert pm.saved.check_saved("edit", "/workspace/main.py") == "allow"
        assert await pm.check_permission("edit", "/workspace/main.py") == "allow"


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
