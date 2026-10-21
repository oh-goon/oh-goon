"""CO-R3H original-owner AST and CORE regression, not an independent cleanup owner."""

from __future__ import annotations

from pathlib import Path

import pytest
from test_co_r01_lifecycle_architecture import _fixture

from oh_goon.repository_tooling.architecture.co_r01_lifecycle_contract import (
    _CGROUP_OWNER,
    _R3_QUALIFIER,
    _R3H_TEST,
    check_co_r01_lifecycle_edges,
)


def test_original_postcut_inode_guard_passes(tmp_path: Path) -> None:
    assert check_co_r01_lifecycle_edges(_fixture(tmp_path)) == []


@pytest.mark.parametrize(
    ("relative", "before", "after"),
    [
        (
            _R3_QUALIFIER,
            "tests/unit/integrations/test_co_r3h_original_inode_retirement.py",
            "tests/unit/integrations/test_unqualified_retirement.py",
        ),
        (
            _CGROUP_OWNER,
            "retired = os.fstat(descriptor)",
            "retired = os.stat(cgroup, follow_symlinks=False)",
        ),
        (_CGROUP_OWNER, "or retired.st_nlink != 0", "or retired.st_nlink < 0"),
        (
            _CGROUP_OWNER,
            "(retired.st_dev, retired.st_ino) != expected_identity",
            "(retired.st_dev, retired.st_ino) == expected_identity",
        ),
        (
            _R3H_TEST,
            "test_foreign_deleted_at_destructive_cut_must_not_ack_original_retirement",
            "test_foreign_deleted_at_destructive_cut_is_accepted",
        ),
    ],
)
def test_postcut_inode_bypass_is_rejected_by_original_architecture(
    tmp_path: Path,
    relative: str,
    before: str,
    after: str,
) -> None:
    root = _fixture(tmp_path)
    file = root / relative
    source = file.read_text(encoding="utf-8")
    assert before in source
    file.write_text(source.replace(before, after, 1), encoding="utf-8")
    assert any(issue.path == file for issue in check_co_r01_lifecycle_edges(root))
