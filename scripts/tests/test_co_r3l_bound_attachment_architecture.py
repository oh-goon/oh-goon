"""CO-R3L source-mutation guard for canonical nested controller/attachment effects."""

from __future__ import annotations

from pathlib import Path

import pytest
from test_co_r01_lifecycle_architecture import _fixture

from oh_goon.repository_tooling.architecture.co_r01_lifecycle_contract import (
    _CGROUP_OWNER,
    _R3_QUALIFIER,
    _R3L_LAUNCHER,
    _R3L_TEST,
    check_co_r01_lifecycle_edges,
)


def test_bound_delegated_native_effects_contract_passes(tmp_path: Path) -> None:
    assert check_co_r01_lifecycle_edges(_fixture(tmp_path)) == []


@pytest.mark.parametrize(
    ("relative", "before", "after"),
    [
        (
            _R3_QUALIFIER,
            "tests/unit/integrations/test_co_r3l_bound_nested_attachment.py",
            "tests/unit/integrations/test_unqualified_nested.py",
        ),
        (
            _CGROUP_OWNER,
            "expected_identity=lease.cgroup_identity",
            "expected_identity=None",
        ),
        (
            _CGROUP_OWNER,
            "nested_identity: tuple[int, int] | None",
            "nested_identity: object | None",
        ),
        (
            _CGROUP_OWNER,
            '"cgroup.subtree_control", payload',
            '"unsafe.subtree_control", payload',
        ),
        (
            _CGROUP_OWNER,
            '"cgroup.procs",\n        f"{pid}\\n".encode("ascii")',
            '"unsafe.procs",\n        f"{pid}\\n".encode("ascii")',
        ),
        (_R3L_LAUNCHER, "identity=monitor_identity", "identity=nested_identity"),
        (_R3L_LAUNCHER, "separator != 6", "separator != 2"),
        (
            _R3L_TEST,
            "test_child_creation_rejects_parent_replacement",
            "test_parent_substitution_is_trusted",
        ),
    ],
)
def test_bound_native_effect_bypass_rejected(
    tmp_path: Path,
    relative: str,
    before: str,
    after: str,
) -> None:
    root = _fixture(tmp_path)
    target = root / relative
    source = target.read_text(encoding="utf-8")
    assert before in source
    target.write_text(source.replace(before, after, 1), encoding="utf-8")
    assert any(issue.path == target for issue in check_co_r01_lifecycle_edges(root))
