"""Instruction file discovery system.

Walks up the directory tree from workspace to find project instruction files
(AGENTS.md, CLAUDE.md). Also supports remote URLs and global config instructions.
Discovered instructions are injected into the system prompt.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

# Well-known instruction file names (checked in order of priority)
INSTRUCTION_FILENAMES = ["AGENTS.md", "CLAUDE.md"]

# Global config directory for user-level instructions
GLOBAL_CONFIG_DIR = Path.home() / ".config" / "codeassist"


@dataclass
class InstructionSource:
    """A single instruction source (file or URL)."""
    path: str  # File path or URL
    content: str = ""
    loaded: bool = False
    load_time: datetime | None = None
    error: str = ""

    @property
    def is_url(self) -> bool:
        return self.path.startswith("http://") or self.path.startswith("https://")

    @property
    def filename(self) -> str:
        if self.is_url:
            return self.path
        return Path(self.path).name


async def load_file_instructions(path: Path, max_size: int = 64 * 1024) -> InstructionSource:
    """Load instructions from a local file."""
    source = InstructionSource(path=str(path))
    try:
        if not path.exists():
            source.error = f"File not found: {path}"
            return source

        stat = path.stat()
        if stat.st_size > max_size:
            source.error = f"File too large ({stat.st_size} bytes, max {max_size}): {path}"
            log.warning(source.error)
            return source

        content = path.read_text(encoding="utf-8", errors="replace")
        source.content = content
        source.loaded = True
        source.load_time = datetime.now(UTC)
        log.info("Loaded instructions from %s (%d bytes)", path, len(content))
    except Exception as e:  # noqa: BLE001
        source.error = f"Failed to load {path}: {e}"
        log.error(source.error)

    return source


async def load_url_instructions(url: str, timeout: float = 10.0) -> InstructionSource:
    """Load instructions from a remote URL."""
    source = InstructionSource(path=url)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url, follow_redirects=True)
            resp.raise_for_status()
            content = resp.text
            if len(content) > 64 * 1024:
                source.error = f"Remote content too large ({len(content)} bytes): {url}"
                log.warning(source.error)
                return source
            source.content = content
            source.loaded = True
            source.load_time = datetime.now(UTC)
            log.info("Loaded remote instructions from %s (%d bytes)", url, len(content))
    except Exception as e:  # noqa: BLE001
        source.error = f"Failed to fetch {url}: {e}"
        log.error(source.error)

    return source


class InstructionDiscoverer:
    """Discovers and loads instruction files for a workspace."""

    def __init__(self):
        self.sources: list[InstructionSource] = []
        self._loaded_paths: set[str] = set()  # Track loaded paths to avoid duplicates

    async def discover(
        self,
        workspace: Path,
        config_paths: list[str] | None = None,
        disable_project_config: bool = False,
    ) -> list[InstructionSource]:
        """Discover and load all instruction sources.

        Args:
            workspace: The workspace root directory.
            config_paths: Additional paths/URLs from config [instructions].paths.
            disable_project_config: If True, skip AGENTS.md/CLAUDE.md discovery.

        Returns:
            List of successfully loaded instruction sources.
        """
        self.sources = []
        self._loaded_paths = set()

        # 1. Discover project-level instructions (AGENTS.md, CLAUDE.md)
        if not disable_project_config:
            project_sources = await self._discover_project_instructions(workspace)
            self.sources.extend(project_sources)

        # 2. Discover global config instructions
        global_sources = await self._discover_global_instructions()
        self.sources.extend(global_sources)

        # 3. Load config-specified paths/URLs
        if config_paths:
            for p in config_paths:
                source = await self._load_single(p)
                if source:
                    self.sources.append(source)

        # Filter to only successfully loaded sources
        loaded = [s for s in self.sources if s.loaded]
        return loaded

    async def _discover_project_instructions(self, workspace: Path) -> list[InstructionSource]:
        """Walk up from workspace to find instruction files."""
        results = []
        current = workspace.resolve()

        while True:
            for filename in INSTRUCTION_FILENAMES:
                candidate = current / filename
                if candidate.exists():
                    source = await self._load_single(str(candidate))
                    if source and source.loaded:
                        results.append(source)

            # Stop at filesystem root or if we've gone up too far
            parent = current.parent
            if parent == current:
                break
            current = parent

        return results

    async def _discover_global_instructions(self) -> list[InstructionSource]:
        """Check global config directory for instructions."""
        results = []
        global_dir = GLOBAL_CONFIG_DIR

        if not global_dir.exists():
            return results

        for filename in INSTRUCTION_FILENAMES:
            candidate = global_dir / filename
            if candidate.exists():
                source = await self._load_single(str(candidate))
                if source and source.loaded:
                    results.append(source)

        return results

    async def _load_single(self, path_or_url: str) -> InstructionSource | None:
        """Load a single instruction source (file or URL)."""
        resolved = Path(path_or_url).resolve() if not path_or_url.startswith("http") else Path(path_or_url)

        # Avoid loading the same file twice
        real_path = str(resolved)
        if real_path in self._loaded_paths:
            return None
        self._loaded_paths.add(real_path)

        if path_or_url.startswith("http"):
            return await load_url_instructions(path_or_url)
        else:
            return await load_file_instructions(Path(path_or_url))

    def get_combined_content(self, sources: list[InstructionSource] | None = None) -> str:
        """Combine all loaded instructions into a single text block."""
        if sources is None:
            sources = [s for s in self.sources if s.loaded]

        if not sources:
            return ""

        parts = []
        for source in sources:
            header = f"## Instructions from {source.filename}"
            parts.append(f"{header}\n\n{source.content}")

        return "\n\n---\n\n".join(parts)

    def get_instruction_block(self, sources: list[InstructionSource] | None = None) -> str:
        """Get formatted instruction block for system prompt injection."""
        content = self.get_combined_content(sources)
        if not content:
            return ""

        return f"""## Project Instructions

The following project-specific instructions were discovered and should be followed:

{content}"""


# Module-level singleton
_instructor: InstructionDiscoverer | None = None


def get_instruction_discoverer() -> InstructionDiscoverer:
    """Get or create the instruction discoverer singleton."""
    global _instructor
    if _instructor is None:
        _instructor = InstructionDiscoverer()
    return _instructor
