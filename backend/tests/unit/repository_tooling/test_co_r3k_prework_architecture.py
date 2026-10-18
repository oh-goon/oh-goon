"""X0 mutation guard: same original owner across cgroup creation and rollback."""
from __future__ import annotations

from pathlib import Path

import pytest
from tests.unit.repository_tooling.test_co_r01_lifecycle_architecture import _fixture

from oh_goon.repository_tooling.architecture.co_r01_lifecycle_contract import (
    _CGROUP_OWNER,
    _R3_QUALIFIER,
    _R3K_TEST,
    check_co_r01_lifecycle_edges,
)


def test_original_prework_cgroup_owner_contract_passes(tmp_path: Path) -> None:
    assert check_co_r01_lifecycle_edges(_fixture(tmp_path)) == []


@pytest.mark.parametrize(('relative', 'before', 'after'), [
    (_R3_QUALIFIER, 'tests/unit/integrations/test_co_r3k_prework_identity_rollback.py',
     'tests/unit/integrations/test_unqualified_prework.py'),
    (_CGROUP_OWNER, '_write_resource_lease(self)\n                _apply_resource_limits(',
     '_apply_resource_limits(self.cgroup, self.policy, expected_identity=self.cgroup_identity)\n                _write_resource_lease(self)\n                _apply_resource_limits('),
    (_CGROUP_OWNER, '_kill_cgroup(self.cgroup, expected_identity=self.cgroup_identity)',
     '_kill_cgroup(self.cgroup)'),
    (_CGROUP_OWNER, '_remove_cgroup(self.cgroup, expected_identity=self.cgroup_identity)',
     '_remove_cgroup(self.cgroup)'),
    (_CGROUP_OWNER, 'os.O_WRONLY | os.O_NOFOLLOW | os.O_CLOEXEC',
     'os.O_WRONLY | os.O_CLOEXEC'),
    (_R3K_TEST, 'test_replaced_path_during_initialization_retains_record_and_foreign_target',
     'test_path_replacement_is_allowed'),
])
def test_prework_bypass_is_rejected(
    tmp_path: Path, relative: str, before: str, after: str,
) -> None:
    root = _fixture(tmp_path)
    path = root / relative
    source = path.read_text(encoding='utf-8')
    assert before in source
    path.write_text(source.replace(before, after, 1), encoding='utf-8')
    assert any(issue.path == path for issue in check_co_r01_lifecycle_edges(root))
