from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path

import yaml

from oh_goon.core.errors import FleetPlanInvalid, LeagueLineupStale
from oh_goon.core.models.shared_plan import LeagueLineupDocument
from oh_goon.runtime.exact_file_store import ExactFileStore
from oh_goon.runtime.league_folders import (
    league_preferences_path_for_environment,
)

LINEUP_VERSION = 1
_LINEUP_KEY = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?$")


def lineup_root_for_environment(environment: Mapping[str, str]) -> Path:
    override = environment.get("OH_GOON_LINEUP_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return (league_preferences_path_for_environment(environment).parent / "lineups").resolve()


def default_lineup_root() -> Path:
    return lineup_root_for_environment(os.environ)


class LeagueLineupFileStore(ExactFileStore):
    """Atomic local Team selections with exact-byte stale-write protection."""

    def __init__(self, root: Path | None = None, *, initialize: bool = True) -> None:
        super().__init__(
            root or default_lineup_root(),
            file_name="lineup.yml",
            key_pattern=_LINEUP_KEY,
            label="League Lineup",
            initialize=initialize,
        )

    def read(
        self,
        lineup_key: str,
        *,
        expected_revision: str | None = None,
    ) -> LeagueLineupDocument:
        key, raw, revision = self._read_exact_bytes(
            lineup_key,
            expected_revision=expected_revision,
        )
        try:
            payload = yaml.safe_load(raw.decode("utf-8"))
        except (UnicodeError, yaml.YAMLError) as exc:
            raise FleetPlanInvalid(f"Cannot read League Lineup {key}: {exc}") from exc
        if not isinstance(payload, dict) or set(payload) != {"version", "name", "teams"}:
            raise FleetPlanInvalid(f"League Lineup {key} must contain only version, name and teams")
        if payload["version"] != LINEUP_VERSION:
            raise FleetPlanInvalid(
                f"League Lineup {key} version {payload['version']!r} is unsupported; expected {LINEUP_VERSION}"
            )
        name = payload["name"]
        teams = payload["teams"]
        if not isinstance(name, str) or not name.strip():
            raise FleetPlanInvalid(f"League Lineup {key} name must be non-empty text")
        if (
            not isinstance(teams, list)
            or not teams
            or any(not isinstance(item, str) for item in teams)
        ):
            raise FleetPlanInvalid(f"League Lineup {key} teams must be a non-empty path list")
        repositories = tuple(Path(item).expanduser() for item in teams)
        if any(not item.is_absolute() for item in repositories):
            raise FleetPlanInvalid(f"League Lineup {key} Team paths must be absolute")
        repositories = tuple(item.resolve() for item in repositories)
        if len(set(repositories)) != len(repositories):
            raise FleetPlanInvalid(f"League Lineup {key} contains duplicate Team paths")
        return LeagueLineupDocument(
            lineup_key=key,
            name=name.strip(),
            team_count=len(repositories),
            revision=revision,
            repositories=repositories,
        )

    def write(
        self,
        lineup_key: str,
        *,
        name: str,
        repositories: tuple[Path, ...],
        expected_revision: str | None = None,
    ) -> LeagueLineupDocument:
        normalized_name = name.strip()
        if not normalized_name:
            raise FleetPlanInvalid("League Lineup name cannot be empty")
        resolved = tuple(path.expanduser().resolve() for path in repositories)
        if not resolved:
            raise FleetPlanInvalid("League Lineup must contain at least one Team")
        if len(set(resolved)) != len(resolved):
            raise FleetPlanInvalid("League Lineup cannot contain duplicate Teams")
        rendered = yaml.safe_dump(
            {
                "version": LINEUP_VERSION,
                "name": normalized_name,
                "teams": [str(item) for item in resolved],
            },
            sort_keys=False,
            allow_unicode=True,
        )
        key, _raw, revision = self._commit_exact_text(
            lineup_key,
            rendered,
            expected_revision=expected_revision,
        )
        return LeagueLineupDocument(
            lineup_key=key,
            name=normalized_name,
            team_count=len(resolved),
            revision=revision,
            repositories=resolved,
        )

    def _normalize_key(self, value: str) -> str:
        return _key(value)

    @staticmethod
    def _invalid_exact_file(key: str) -> Exception:
        return FleetPlanInvalid(f"League Lineup must be a regular file: {key}/lineup.yml")

    @staticmethod
    def _changed_stale(_key: str) -> Exception:
        return LeagueLineupStale(
            "League Lineup changed before save; reload the current Lineup revision"
        )

    @staticmethod
    def _removed_stale(_key: str) -> Exception:
        return LeagueLineupStale("League Lineup was removed before save; reload the Lineup catalog")


def _key(value: str) -> str:
    key = value.strip().lower()
    if not _LINEUP_KEY.fullmatch(key):
        raise FleetPlanInvalid(
            "League Lineup key must contain only lowercase letters, digits, '.', '_' or '-'"
        )
    return key
