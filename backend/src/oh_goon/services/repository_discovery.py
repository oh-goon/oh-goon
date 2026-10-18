from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from oh_goon.core.models.repository import (
    ExcludedRepositoryCatalogEntry,
    IncludedRepositoryCatalogEntry,
    RepositoryCatalogEntry,
    RepositoryIdentity,
)
from oh_goon.core.vocabulary import FleetScope
from oh_goon.core.path_authority import navigation_path


class RepositoryDiscoveryService:
    """Own repository-root traversal and Fleet catalog discovery."""

    def discover(
        self,
        locations: list[Path] | tuple[Path, ...],
        exclusions: Mapping[str, Mapping[str, str]],
    ) -> list[RepositoryCatalogEntry]:
        return [
            self._describe(candidate, exclusions)
            for candidate in self.git_roots(locations, require_pyproject=False)
        ]

    def git_roots(
        self,
        locations: list[Path] | tuple[Path, ...],
        *,
        require_pyproject: bool,
    ) -> tuple[Path, ...]:
        """Discover Git repository roots using the one filesystem traversal policy."""
        repositories: set[Path] = set()
        for location in locations:
            repositories.update(self._candidates(location, require_pyproject=require_pyproject))
        return tuple(sorted(repositories, key=lambda path: (str(path).casefold(), str(path))))

    def containing_git_root(
        self,
        locations: list[Path] | tuple[Path, ...],
        requested: Path,
    ) -> Path | None:
        """Resolve one known path using the same top-down policy as League discovery."""
        path = requested.expanduser().resolve()
        current = path if path.is_dir() else path.parent
        discovered: set[Path] = set()
        for location in locations:
            root = location.expanduser().resolve()
            if not root.is_dir() or not (current == root or current.is_relative_to(root)):
                continue
            relative_parts = current.relative_to(root).parts
            candidate = root
            for depth in range(0, len(relative_parts) + 1):
                if (candidate / ".git").exists():
                    discovered.add(candidate)
                    break
                if depth == len(relative_parts):
                    break
                candidate /= relative_parts[depth]
        if not discovered:
            return None
        return max(discovered, key=lambda candidate: len(candidate.parts))

    @staticmethod
    def _describe(
        candidate: Path,
        exclusions: Mapping[str, Mapping[str, str]],
    ) -> RepositoryCatalogEntry:
        exclusion = exclusions.get(str(navigation_path(candidate)))
        identity = RepositoryIdentity(name=candidate.name, path=candidate)
        if exclusion is None:
            return IncludedRepositoryCatalogEntry(scope=FleetScope.INCLUDED, repository=identity)
        return ExcludedRepositoryCatalogEntry(
            scope=FleetScope.EXCLUDED,
            repository=identity,
            exclusion_reason=exclusion["reason"],
            excluded_at=exclusion["excluded_at"],
        )

    def _candidates(self, location: Path, *, require_pyproject: bool) -> list[Path]:
        location = location.expanduser().resolve()
        if not location.is_dir():
            return []
        candidates: list[Path] = []
        for current, directories, files in os.walk(location):
            path = Path(current)
            self._prune(path, directories)
            if not (path / ".git").exists():
                continue
            if require_pyproject and "pyproject.toml" not in files:
                continue
            candidates.append(path)
            directories[:] = []
        return candidates

    @staticmethod
    def _prune(current: Path, directories: list[str]) -> None:
        """Avoid descending only into Git administrative state.

        A configured League root means all descendant Git roots are eligible Teams.
        Incidental ancestor names (including hidden/build/cache-like names) must not
        redefine repository identity. Once a Git root is found, _candidates stops
        descent into that repository separately.
        """

        directories[:] = [name for name in directories if name != ".git"]
