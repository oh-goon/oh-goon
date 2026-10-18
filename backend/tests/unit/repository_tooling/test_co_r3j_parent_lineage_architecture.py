"""Original X0 mutation guard for CO-R3J parent-lineage retirement fences."""
from __future__ import annotations

from pathlib import Path

import pytest
from tests.unit.repository_tooling.test_co_r01_lifecycle_architecture import _fixture

from oh_goon.repository_tooling.architecture.co_r01_lifecycle_contract import (
    _CGROUP_OWNER,
    _R3_QUALIFIER,
    _R3J_TEST,
    check_co_r01_lifecycle_edges,
)


def test_original_parent_lineage_contract_passes(tmp_path: Path) -> None:
    assert check_co_r01_lifecycle_edges(_fixture(tmp_path)) == []


@pytest.mark.parametrize(('relative', 'before', 'after'), [
    (_R3_QUALIFIER, 'tests/unit/integrations/test_co_r3j_original_parent_lineage.py',
     'tests/unit/integrations/test_unqualified_parent.py'),
    (_CGROUP_OWNER, '_require_original_cgroup_parent(cgroup.parent, parent_fd, original_parent)',
     '_unqualified_parent(cgroup.parent, parent_fd, original_parent)'),
    (_CGROUP_OWNER, 'current = os.stat(parent, follow_symlinks=False)',
     'current = os.stat(parent, follow_symlinks=True)'),
    (_CGROUP_OWNER, '(current.st_dev, current.st_ino) != expected_identity',
     '(current.st_dev, current.st_ino) == expected_identity'),
    (_R3J_TEST, 'test_parent_replaced_at_native_rmdir_cannot_ack_success',
     'test_parent_replacement_is_trusted'),
])
def test_parent_lineage_bypass_rejected(
    tmp_path: Path, relative: str, before: str, after: str,
) -> None:
    root = _fixture(tmp_path)
    path = root / relative
    source = path.read_text(encoding='utf-8')
    assert before in source
    path.write_text(source.replace(before, after, 1), encoding='utf-8')
    assert any(issue.path == path for issue in check_co_r01_lifecycle_edges(root))
