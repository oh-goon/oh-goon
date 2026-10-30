from __future__ import annotations

import builtins
from collections.abc import Callable
from pathlib import Path

from oh_goon.core.errors import LocationNotDirectory
from oh_goon.runtime.state import LocalState, locations_revision


class LocationService:
    """Own durable configured repository locations."""

    def __init__(self, state: LocalState) -> None:
        self._state = state

    def snapshot(self) -> tuple[list[Path], list[Path], str]:
        """Project one coherent configured-location cut and its derived identity."""
        locations = self._state.read_view().locations
        return (
            list(locations),
            [path for path in locations if not path.is_dir()],
            locations_revision(locations),
        )

    def list(self) -> list[Path]:
        return self.snapshot()[0]

    def unavailable(self) -> builtins.list[Path]:
        """Return configured roots that are currently not accessible directories."""
        return self.snapshot()[1]

    def add(self, path: Path) -> builtins.list[Path]:
        resolved = path.expanduser().resolve()
        if not resolved.is_dir():
            raise LocationNotDirectory("Location must be an existing directory")
        return self._state.add_location(resolved)

    def register_target(
        self,
        path: Path,
        *,
        validate: Callable[[tuple[Path, ...], frozenset[str]], None],
    ) -> dict[str, str]:
        """Keep explicit workflow registration inside the original scope mutation."""
        from oh_goon.core.models.repository import RepositoryIdentity

        already_registered = False
        observed = False

        def observe(folders: tuple[Path, ...], exclusions: frozenset[str]) -> None:
            nonlocal already_registered, observed
            validate(folders, exclusions)
            if not observed:
                already_registered = path in folders
                observed = True

        self._state.add_location(path, validate=observe)
        return {
            "path": str(path),
            "repository_id": RepositoryIdentity.model_validate({"path": path}).repository_id,
            "status": "already-registered" if already_registered else "registered",
        }

    def remove(self, path: Path, *, expected_revision: str) -> builtins.list[Path]:
        return self._state.remove_location(path, expected_revision=expected_revision)
