from __future__ import annotations

from pathlib import Path

import pytest

from oh_goon.integrations.exact_file_write import require_expected_revision
from oh_goon.runtime.exact_file_store import ExactFileStore
from oh_goon.runtime.league_lineups import LeagueLineupFileStore
from oh_goon.runtime.shared_plans import SharedPlanFileStore


def test_local_yaml_stores_share_one_exact_file_store_owner() -> None:
    assert issubclass(SharedPlanFileStore, ExactFileStore)
    assert issubclass(LeagueLineupFileStore, ExactFileStore)


def test_revision_proof_accepts_only_unfenced_create_or_exact_match() -> None:
    def changed(current, expected):
        return RuntimeError(f"changed:{current}:{expected}")

    def removed():
        return RuntimeError("removed")

    require_expected_revision(None, None, changed_error=changed, removed_error=removed)
    require_expected_revision("a" * 64, "a" * 64, changed_error=changed, removed_error=removed)

    with pytest.raises(RuntimeError, match="changed"):
        require_expected_revision("a" * 64, None, changed_error=changed, removed_error=removed)
    with pytest.raises(RuntimeError, match="changed"):
        require_expected_revision(
            "a" * 64,
            "b" * 64,
            changed_error=changed,
            removed_error=removed,
        )
    with pytest.raises(RuntimeError, match="removed"):
        require_expected_revision(
            None,
            "a" * 64,
            changed_error=changed,
            removed_error=removed,
        )


def test_shared_plan_fenced_read_uses_store_revision_authority(tmp_path: Path) -> None:
    store = SharedPlanFileStore(tmp_path / "plans")
    original = store.write("python-quality", "name: python-quality\ngoons: []\n")
    store.write(
        "python-quality",
        "name: python-quality\ngoons: [python/test]\n",
        expected_revision=original.sha256,
    )

    with pytest.raises(Exception, match="changed since it was read"):
        store.read("python-quality", expected_revision=original.sha256)
