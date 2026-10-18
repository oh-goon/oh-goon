"""CO-R3E mutation tests: legacy orphan recovery cannot bypass original cgroup pin."""
from __future__ import annotations

from pathlib import Path

import pytest
from tests.unit.repository_tooling.test_co_r01_lifecycle_architecture import _fixture

from oh_goon.repository_tooling.architecture.co_r01_lifecycle_contract import (
    _CGROUP_OWNER,
    _R3_QUALIFIER,
    _R3E_TEST,
    check_co_r01_lifecycle_edges,
)


def test_original_recovery_mutation_guard_is_active(tmp_path: Path) -> None:
    assert check_co_r01_lifecycle_edges(_fixture(tmp_path)) == []


@pytest.mark.parametrize(('relative', 'before', 'after'), [
    (_R3_QUALIFIER, 'tests/unit/integrations/test_co_r3e_durable_cgroup_recovery.py',
     'tests/unit/integrations/test_fake_orphan.py'),
    (_CGROUP_OWNER, '"cgroup_dev": lease.cgroup_identity[0]',
     '"cgroup_dev": 1'),
    (_CGROUP_OWNER, '"cgroup_ino": lease.cgroup_identity[1]',
     '"cgroup_ino": 1'),
    (_CGROUP_OWNER, 'type(device) is not int', 'not isinstance(device, object)'),
    (_CGROUP_OWNER, '_require_original_cgroup(cgroup, (device, inode))',
     'pass  # bypass original cgroup identity fence'),
    (_CGROUP_OWNER, 'Fleet legacy orphan lacks independent physical custody',
     'Fleet legacy orphan has no custody requirement'),
    (_R3E_TEST, 'test_foreign_replacement_cannot_be_killed_or_removed',
     'test_foreign_replacement_not_tested'),
])
def test_original_recovery_bypass_detected(
    tmp_path: Path, relative: str, before: str, after: str,
) -> None:
    root = _fixture(tmp_path)
    target = root / relative
    original = target.read_text(encoding='utf-8')
    assert before in original
    target.write_text(original.replace(before, after, 1), encoding='utf-8')
    assert any(issue.path == target for issue in check_co_r01_lifecycle_edges(root))
