"""CO-R3D source-mutation guard: no second owner or false native quiescence."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.unit.repository_tooling.test_co_r01_lifecycle_architecture import _fixture

from oh_goon.repository_tooling.architecture.co_r01_lifecycle_contract import (
    _CGROUP_OWNER,
    _R3_QUALIFIER,
    _R3D_TEST,
    check_co_r01_lifecycle_edges,
)


def test_original_cgroup_contract_enrolled_without_second_owner(tmp_path: Path) -> None:
    assert check_co_r01_lifecycle_edges(_fixture(tmp_path)) == []


@pytest.mark.parametrize(
    ("relative", "old", "new"),
    [
        (
            _R3_QUALIFIER,
            "tests/unit/integrations/test_co_r3d_original_cgroup_quiescence.py",
            "tests/unit/integrations/test_bypassed_cgroup_owner.py",
        ),
        (
            _CGROUP_OWNER,
            "_wait_for_cgroup_empty(self.cgroup, expected_identity=self.cgroup_identity)",
            "_wait_for_cgroup_empty(self.cgroup)",
        ),
        (
            _CGROUP_OWNER,
            "_remove_cgroup(self.cgroup, expected_identity=self.cgroup_identity)",
            "_remove_unknown_group(self.cgroup, expected_identity=self.cgroup_identity)",
        ),
        (
            _CGROUP_OWNER,
            "write_attempt_resource_evidence(self.attempt_root, evidence)",
            "publish_before_teardown(self.attempt_root, evidence)",
        ),
        (_CGROUP_OWNER, '"cgroup.events", 4096', '"optional.events", 4096'),
        (_CGROUP_OWNER, '"cgroup.procs", 65536', '"optional.procs", 65536'),
        (
            _R3D_TEST,
            "test_original_owner_never_publishes_completion_if_native_remove_fails",
            "test_missing_native_failure_control",
        ),
    ],
)
def test_physical_quiescence_downgrade_fails_original_architecture(
    tmp_path: Path,
    relative: str,
    old: str,
    new: str,
) -> None:
    root = _fixture(tmp_path)
    file = root / relative
    source = file.read_text(encoding="utf-8")
    assert old in source
    if relative == _CGROUP_OWNER and old.startswith("_remove_cgroup("):
        prefix, signature, closing = source.partition("    def close(self)")
        assert signature and old in closing
        source = prefix + signature + closing.replace(old, new, 1)
    else:
        source = source.replace(old, new, 1)
    file.write_text(source, encoding="utf-8")
    assert any(issue.path == file for issue in check_co_r01_lifecycle_edges(root))
