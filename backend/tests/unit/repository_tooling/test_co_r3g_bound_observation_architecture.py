"""CO-R3G original-owner AST/CORE enrollment negative mutation guard."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.unit.repository_tooling.test_co_r01_lifecycle_architecture import _fixture

from oh_goon.repository_tooling.architecture.co_r01_lifecycle_contract import (
    _CGROUP_OWNER,
    _R3_QUALIFIER,
    _R3G_TEST,
    check_co_r01_lifecycle_edges,
)


def test_original_bound_observation_guard_passes(tmp_path: Path) -> None:
    assert check_co_r01_lifecycle_edges(_fixture(tmp_path)) == []


@pytest.mark.parametrize(
    ("relative", "before", "after"),
    [
        (
            _R3_QUALIFIER,
            "tests/unit/integrations/test_co_r3g_bound_observation_removal.py",
            "tests/unit/integrations/test_unsafe_observation.py",
        ),
        (
            _CGROUP_OWNER,
            "os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=descriptor",
            "os.O_RDONLY | os.O_CLOEXEC",
        ),
        (
            _CGROUP_OWNER,
            'events = _read_bound_cgroup_control(descriptor, "cgroup.events", 4096)',
            'events = (cgroup / "cgroup.events").read_text().splitlines()',
        ),
        (_CGROUP_OWNER, "os.rmdir(cgroup.name, dir_fd=parent_fd)", "os.rmdir(cgroup)"),
        (
            _CGROUP_OWNER,
            "Fleet native cgroup target reappeared after removal",
            "Fleet post-cut reappearance ignored",
        ),
        (
            _R3G_TEST,
            "test_replacement_before_remove_refuses_native_effect",
            "test_replacement_before_remove_allows_native_effect",
        ),
    ],
)
def test_original_bound_owner_bypass_is_rejected(
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
