from __future__ import annotations

import os
import sys
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path

import yaml

from oh_goon.core.errors import LeaguePreferencesInvalid
from oh_goon.core.path_authority import ensure_private_directory
from oh_goon.integrations.exact_file_write import (
    exact_file_write_lock,
    prepared_text_file,
    replace_prepared_file,
)

LEAGUE_PREFERENCES_VERSION = 1
LEAGUE_PREFERENCES_FILENAME = "league.yml"


def league_preferences_path_for_environment(environment: Mapping[str, str]) -> Path:
    override = environment.get("OH_GOON_LEAGUE_FILE")
    if override:
        return Path(override).expanduser().resolve()
    database_override = environment.get("OH_GOON_DB")
    if database_override:
        return Path(database_override).expanduser().resolve().parent / LEAGUE_PREFERENCES_FILENAME
    if os.name == "nt" and environment.get("APPDATA"):
        base = Path(environment["APPDATA"])
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        config_home = environment.get("XDG_CONFIG_HOME")
        base = Path(config_home).expanduser() if config_home else Path.home() / ".config"
    return (base / "oh-goon" / LEAGUE_PREFERENCES_FILENAME).resolve()


def default_league_preferences_path() -> Path:
    return league_preferences_path_for_environment(os.environ)


class LeagueFolderStore:
    """Durable user-owned League-folder preferences, independent of disposable run history."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = (path or default_league_preferences_path()).expanduser().resolve()

    def list(self) -> tuple[Path, ...]:
        if not self.path.exists():
            return ()
        try:
            payload = yaml.safe_load(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            raise LeaguePreferencesInvalid(
                f"Cannot read League preferences at {self.path}: {exc}"
            ) from exc
        if not isinstance(payload, dict) or set(payload) != {"version", "folders"}:
            raise LeaguePreferencesInvalid(
                f"League preferences at {self.path} must contain only version and folders"
            )
        if payload["version"] != LEAGUE_PREFERENCES_VERSION:
            raise LeaguePreferencesInvalid(
                f"League preferences version {payload['version']!r} is unsupported; "
                f"expected {LEAGUE_PREFERENCES_VERSION}"
            )
        folders = payload["folders"]
        if not isinstance(folders, list) or any(not isinstance(item, str) for item in folders):
            raise LeaguePreferencesInvalid("League preferences folders must be a list of paths")
        expanded = tuple(Path(item).expanduser() for item in folders)
        if any(not item.is_absolute() for item in expanded):
            raise LeaguePreferencesInvalid("League preference folders must be absolute paths")
        resolved = tuple(item.resolve() for item in expanded)
        if len(set(resolved)) != len(resolved):
            raise LeaguePreferencesInvalid("League preferences contain duplicate folders")
        return resolved

    @contextmanager
    def write_lock(self) -> Iterator[None]:
        """Serialize cooperating preference writers for this exact league.yml."""
        with exact_file_write_lock(self.path):
            yield

    def replace(self, folders: tuple[Path, ...]) -> tuple[Path, ...]:
        with self.write_lock():
            return self.replace_locked(folders)

    def replace_locked(self, folders: tuple[Path, ...]) -> tuple[Path, ...]:
        """Replace preferences while the caller owns write_lock()."""
        resolved = tuple(folder.expanduser().resolve() for folder in folders)
        if len(set(resolved)) != len(resolved):
            raise LeaguePreferencesInvalid("League preferences contain duplicate folders")
        self._write_locked(resolved)
        return resolved

    def _write_locked(self, folders: tuple[Path, ...]) -> None:
        ensure_private_directory(self.path.parent)
        payload = {
            "version": LEAGUE_PREFERENCES_VERSION,
            "folders": [str(folder) for folder in folders],
        }
        rendered = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)
        try:
            with prepared_text_file(self.path, rendered) as temporary:
                replace_prepared_file(temporary, self.path)
        except OSError as exc:
            raise LeaguePreferencesInvalid(
                f"Cannot write League preferences at {self.path}: {exc}"
            ) from exc
