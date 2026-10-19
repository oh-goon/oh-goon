"""CO-R3F structural regression of original cgroup handle ownership."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.unit.repository_tooling.test_co_r01_lifecycle_architecture import _fixture

from oh_goon.repository_tooling.architecture.co_r01_lifecycle_contract import (
    _CGROUP_OWNER,
    _R3_QUALIFIER,
    _R3F_TEST,
    check_co_r01_lifecycle_edges,
)


def test_original_cgroup_handle_guard_passes(tmp_path: Path) -> None:
    assert check_co_r01_lifecycle_edges(_fixture(tmp_path)) == []


@pytest.mark.parametrize(
    ("relative", "before", "after"),
    [
        (
            _R3_QUALIFIER,
            "tests/unit/integrations/test_co_r3f_cgroup_native_handle.py",
            "tests/unit/integrations/test_weaker_cgroup.py",
        ),
        (
            _CGROUP_OWNER,
            "_kill_cgroup(self.cgroup, expected_identity=self.cgroup_identity)",
            "_kill_cgroup(self.cgroup)",
        ),
        (_CGROUP_OWNER, "dir_fd=descriptor,", "dir_fd=None,"),
        (_CGROUP_OWNER, "os.fstat(control)", 'os.stat(cgroup / "cgroup.kill")'),
        (
            _R3F_TEST,
            "test_missing_or_symlink_control_refuses_pid_fallback",
            "test_missing_or_symlink_control_accepts_pid_fallback",
        ),
    ],
)
def test_handle_bypass_rejected_by_original_architecture(
    tmp_path: Path,
    relative: str,
    before: str,
    after: str,
) -> None:
    root = _fixture(tmp_path)
    file = root / relative
    source = file.read_text(encoding="utf-8")
    assert before in source
    # The current owner also uses these primitives during initialization;
    # mutate specifically the original close / kill owner, not the first
    # unrelated occurrence introduced in later checkpoints.
    pivot = "    def close(self)" if "self.cgroup" in before else "def _kill_cgroup("
    if relative == _CGROUP_OWNER:
        idx = source.index(pivot)
        assert before in source[idx:]
        source = source[:idx] + source[idx:].replace(before, after, 1)
    else:
        source = source.replace(before, after, 1)
    file.write_text(source, encoding="utf-8")
    assert any(issue.path == file for issue in check_co_r01_lifecycle_edges(root))
