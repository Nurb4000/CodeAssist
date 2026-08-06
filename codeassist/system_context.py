"""Composable system context from multiple sources.

Built-in sources: date, environment, instructions, skills.
Changes produce mid-conversation system messages when baseline changes.
Registry pattern allows plugins to add custom context sources.
"""

import logging
import sys
from datetime import date
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)


class ContextSource:
    """Base class for a system context source."""

    name: str = "custom"
    priority: int = 100  # Lower = earlier in prompt

    async def get_context(self, workspace: Path, config: Any = None) -> str:
        """Return context text to inject into system prompt."""
        return ""

    def should_refresh(self, workspace: Path, config: Any = None) -> bool:
        """Check if context needs refreshing (e.g., date changed)."""
        return False


class DateSource(ContextSource):
    """Provides current date information."""
    name = "date"
    priority = 10

    def __init__(self):
        self._last_date = None

    async def get_context(self, workspace: Path, config: Any = None) -> str:
        today = date.today().isoformat()
        self._last_date = today
        return f"Today's date: {today}"

    def should_refresh(self, workspace: Path, config: Any = None) -> bool:
        today = date.today().isoformat()
        if self._last_date != today:
            self._last_date = today
            return True
        return False


class EnvironmentSource(ContextSource):
    """Provides environment information."""
    name = "environment"
    priority = 5

    async def get_context(self, workspace: Path, config: Any = None) -> str:
        return f"""<env>
  Working directory: {workspace}
  Platform: {sys.platform}
  Python: {sys.version.split()[0]}
</env>"""


class InstructionsSource(ContextSource):
    """Provides discovered project instructions (AGENTS.md, etc.)."""
    name = "instructions"
    priority = 20

    def __init__(self):
        self._content = ""
        self._hash = None

    async def get_context(self, workspace: Path, config: Any = None) -> str:
        if not self._content:
            return ""
        return self._content

    def update(self, content: str):
        """Update instructions content (called by instruction_discovery)."""
        self._content = content
        self._hash = hash(content)

    def should_refresh(self, workspace: Path, config: Any = None) -> bool:
        return False  # Instructions don't change mid-session typically


class SkillsSource(ContextSource):
    """Provides available skills list."""
    name = "skills"
    priority = 30

    def __init__(self):
        self._content = ""
        self._hash = None

    async def get_context(self, workspace: Path, config: Any = None) -> str:
        if not self._content:
            return ""
        return self._content

    def update(self, content: str):
        """Update skills guidance (called when skills are reloaded)."""
        self._content = content
        self._hash = hash(content)

    def should_refresh(self, workspace: Path, config: Any = None) -> bool:
        return False


class SystemContextManager:
    """Manages composable system context from multiple sources.

    Tracks context epoch: when baseline changes, emits mid-conversation update.
    """

    def __init__(self):
        self._sources: dict[str, ContextSource] = {}
        self._epoch: int = 0
        self._last_combined: str = ""

    def register(self, source: ContextSource):
        """Register a context source."""
        self._sources[source.name] = source

    def unregister(self, name: str):
        """Unregister a context source by name."""
        self._sources.pop(name, None)

    def get_source(self, name: str) -> ContextSource | None:
        """Get a registered context source."""
        return self._sources.get(name)

    async def build_context(self, workspace: Path, config: Any = None) -> str:
        """Build combined system context from all sources, sorted by priority.

        Returns the combined context string and increments epoch if changed.
        """
        parts = []
        for source in sorted(self._sources.values(), key=lambda s: s.priority):
            try:
                content = await source.get_context(workspace, config)
                if content:
                    parts.append(content)
            except Exception as e:
                log.error("Context source '%s' failed: %s", source.name, e)

        combined = "\n\n".join(parts)

        # Track epoch changes
        if combined != self._last_combined:
            self._epoch += 1
            self._last_combined = combined
            log.debug("System context epoch changed to %d", self._epoch)

        return combined

    @property
    def epoch(self) -> int:
        """Current context epoch. Increments when baseline changes."""
        return self._epoch

    async def check_for_updates(self, workspace: Path, config: Any = None) -> str | None:
        """Check if any source needs refreshing. Returns updated context or None."""
        for source in self._sources.values():
            if source.should_refresh(workspace, config):
                log.info("Context source '%s' needs refresh", source.name)
                return await self.build_context(workspace, config)
        return None

    def get_epoch_change_message(self, new_context: str) -> str:
        """Generate a mid-conversation system message for epoch changes."""
        return f"[System context updated (epoch {self._epoch})]\n\n{new_context}"


# Module-level singleton
_context_manager: SystemContextManager | None = None


def get_context_manager() -> SystemContextManager:
    """Get or create the system context manager with default sources."""
    global _context_manager
    if _context_manager is None:
        _context_manager = SystemContextManager()
        # Register built-in sources
        _context_manager.register(EnvironmentSource())
        _context_manager.register(DateSource())
        _context_manager.register(InstructionsSource())
        _context_manager.register(SkillsSource())
    return _context_manager
