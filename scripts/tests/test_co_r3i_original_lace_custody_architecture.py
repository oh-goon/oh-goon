"""CO-R3I: original LACE reaper must call original native cgroup owner only."""

from __future__ import annotations

from pathlib import Path

import pytest
from test_co_r01_lifecycle_architecture import _fixture

from oh_goon.repository_tooling.architecture.co_r01_lifecycle_contract import (
    _R3_QUALIFIER,
    _R3I_REAPER,
    _R3I_TEST,
    check_co_r01_lifecycle_edges,
)


def test_lace_native_original_owner_architecture_passes(tmp_path: Path) -> None:
    assert check_co_r01_lifecycle_edges(_fixture(tmp_path)) == []


@pytest.mark.parametrize(
    ("relative", "old", "new"),
    [
        (
            _R3_QUALIFIER,
            "tests/unit/integrations/test_co_r3i_original_lace_cgroup_custody.py",
            "tests/unit/integrations/test_unqualified_cgroup_custody.py",
        ),
        (_R3I_REAPER, "_wait_for_cgroup_empty(", "_unqualified_empty("),
        (
            _R3I_REAPER,
            "_bound_cgroup_directory(cgroup, original_identity)",
            "_unbound_cgroup_directory(cgroup, original_identity)",
        ),
        (
            _R3I_REAPER,
            "_remove_cgroup(cgroup, expected_identity=original_identity)",
            "cgroup.rmdir()",
        ),
        (_R3I_REAPER, "expected_identity=original_identity", "expected_identity=None"),
        (
            _R3I_REAPER,
            "owner.execute_native_cleanup(ticket, native_action)",
            "native_action()",
        ),
        (
            _R3I_TEST,
            "test_lace_reaper_cannot_ack_foreign_directory_deleted_at_rmdir_cut",
            "test_lace_reaper_allows_wrong_target_deletion",
        ),
    ],
)
def test_bypass_is_rejected(tmp_path: Path, relative: str, old: str, new: str) -> None:
    root = _fixture(tmp_path)
    path = root / relative
    original = path.read_text(encoding="utf-8")
    assert old in original
    path.write_text(original.replace(old, new, 1), encoding="utf-8")
    assert any(issue.path == path for issue in check_co_r01_lifecycle_edges(root))
