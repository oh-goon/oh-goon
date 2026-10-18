from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path

from oh_goon.core.errors import FleetPlanInvalid, FleetPlanSourceStale
from oh_goon.core.models.shared_plan import SharedPlanDocument
from oh_goon.runtime.exact_file_store import ExactFileStore
from oh_goon.runtime.league_folders import (
    default_league_preferences_path,
    league_preferences_path_for_environment,
)

_PLAN_KEY = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def shared_plan_root_for_environment(environment: Mapping[str, str]) -> Path:
    override = environment.get("OH_GOON_SHARED_PLAN_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return (league_preferences_path_for_environment(environment).parent / "plans").resolve()


def default_shared_plan_root() -> Path:
    return shared_plan_root_for_environment(os.environ)


class SharedPlanFileStore(ExactFileStore):
    """Local user-owned plan.yml files, independent of repository contents and run-history SQLite."""

    def __init__(self, root: Path | None = None) -> None:
        super().__init__(
            root or default_shared_plan_root(),
            file_name="plan.yml",
            key_pattern=_PLAN_KEY,
            label="Shared Plan",
        )

    def read(
        self,
        plan_key: str,
        *,
        expected_revision: str | None = None,
    ) -> SharedPlanDocument:
        key, raw, revision = self._read_exact_bytes(
            plan_key,
            expected_revision=expected_revision,
        )
        try:
            content = raw.decode("utf-8")
        except UnicodeError as exc:
            raise FleetPlanInvalid(f"Cannot read Shared Plan {key}: {exc}") from exc
        if not raw:
            raise FleetPlanInvalid(f"Shared Plan is empty: {key}")
        # Name is filled by the service after parsing; the store owns exact bytes only.
        return SharedPlanDocument(
            plan_key=key,
            sha256=revision,
            byte_count=len(raw),
            content=content,
        )

    def write(
        self,
        plan_key: str,
        content: str,
        *,
        expected_revision: str | None = None,
    ) -> SharedPlanDocument:
        if not content:
            raise FleetPlanInvalid("Shared Plan plan.yml cannot be empty")
        key, raw, revision = self._commit_exact_text(
            plan_key,
            content,
            expected_revision=expected_revision,
        )
        return SharedPlanDocument(
            plan_key=key,
            sha256=revision,
            byte_count=len(raw),
            content=content,
        )

    def _normalize_key(self, value: str) -> str:
        return normalize_shared_plan_key(value)

    @staticmethod
    def _invalid_exact_file(key: str) -> Exception:
        return FleetPlanInvalid(f"Shared Plan must be a regular file: {key}/plan.yml")

    @staticmethod
    def _changed_stale(_key: str) -> Exception:
        return FleetPlanSourceStale(
            "Shared Plan changed since it was read; reload the current SHA-256 revision"
        )

    @staticmethod
    def _removed_stale(_key: str) -> Exception:
        return FleetPlanSourceStale(
            "Shared Plan was removed before save; reload the Shared Plans catalog"
        )


def normalize_shared_plan_key(value: str) -> str:
    key = value
    if not _PLAN_KEY.fullmatch(key):
        raise FleetPlanInvalid("Shared Plan names use only lowercase letters, numbers, and '-'")
    return key
