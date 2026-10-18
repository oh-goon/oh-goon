"""CO-R3M original-owner and original-qualifier mutation guard."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.unit.repository_tooling.test_co_r01_lifecycle_architecture import _fixture

from oh_goon.repository_tooling.architecture.co_r01_lifecycle_contract import (
    _CGROUP_OWNER,
    _R3_QUALIFIER,
    _R3M_TEST,
    check_co_r01_lifecycle_edges,
)


def test_pending_allocation_original_contract_passes(tmp_path: Path) -> None:
    assert check_co_r01_lifecycle_edges(_fixture(tmp_path)) == []


@pytest.mark.parametrize(
    ("relative", "before", "after"),
    [
        (_CGROUP_OWNER, "_write_pending_resource_lease(self)", "pass # bypass pending"),
        (_CGROUP_OWNER, '"phase": "allocation_pending"', '"phase": "identity_bound"'),
        (_CGROUP_OWNER, 'if phase == "allocation_pending":', 'if phase == "allowed":'),
        (
            _CGROUP_OWNER,
            "if lease.state_file.exists() or lease.state_file.is_symlink():",
            "if False:",
        ),
        (
            _R3_QUALIFIER,
            "tests/unit/integrations/test_co_r3m_pending_cgroup_allocation.py",
            "tests/unit/integrations/test_cgroup_pending_skipped.py",
        ),
        (
            _R3M_TEST,
            "test_real_child_exit_between_mkdir_and_identity_publication",
            "test_child_exit_safe_without_test",
        ),
    ],
)
def test_pending_allocation_bypass_rejected(
    tmp_path: Path,
    relative: str,
    before: str,
    after: str,
) -> None:
    root = _fixture(tmp_path)
    target = root / relative
    content = target.read_text(encoding="utf-8")
    assert before in content
    target.write_text(content.replace(before, after, 1), encoding="utf-8")
    assert any(issue.path == target for issue in check_co_r01_lifecycle_edges(root))
