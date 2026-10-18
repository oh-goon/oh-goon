"""Mutate the original X0 gate's CO-R3B witness and verify failure early."""
from __future__ import annotations

from pathlib import Path

import pytest
from tests.unit.repository_tooling.test_co_r01_lifecycle_architecture import _fixture

from oh_goon.repository_tooling.architecture.co_r01_lifecycle_contract import (
    _R3_QUALIFIER,
    _R3B_TEST,
    check_co_r01_lifecycle_edges,
)


def test_original_x0_enforces_all_r3b_failure_cuts(tmp_path: Path) -> None:
    assert check_co_r01_lifecycle_edges(_fixture(tmp_path)) == []


@pytest.mark.parametrize(('relative', 'old', 'new'), [
    (_R3_QUALIFIER, 'tests/unit/runtime/test_co_r3b_original_failure_cuts.py',
     'tests/unit/runtime/test_co_r3b_inert_placeholder.py'),
    (_R3_QUALIFIER, 'tests/unit/repository_tooling/test_co_r3b_original_failure_architecture.py',
     'tests/unit/repository_tooling/test_co_r3b_inert_placeholder.py'),
    (_R3B_TEST, 'os._exit(191)', 'raise RuntimeError("no native death")'),
    (_R3B_TEST, '_admit(db, run_id, intent, scope, repo)', 'pass # no real admission'),
    (_R3B_TEST, '_accept(authority, workspace, assignment, lease, materialization)',
     'pass # acceptance bypassed'),
    (_R3B_TEST, 'owner.export_verified_backup(target)', 'pass # no provider-native backup'),
    (_R3B_TEST, 'os._exit(197)', 'raise RuntimeError("no postlink death")'),
])
def test_native_failure_cut_bypass_fails_original_x0(
    tmp_path: Path, relative: str, old: str, new: str,
) -> None:
    root = _fixture(tmp_path)
    target = root / relative
    content = target.read_text(encoding='utf-8')
    assert old in content
    target.write_text(content.replace(old, new, 1), encoding='utf-8')
    assert any(issue.path == target for issue in check_co_r01_lifecycle_edges(root))
