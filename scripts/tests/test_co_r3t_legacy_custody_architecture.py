"""Prevent silent reintroduction of legacy PID-scavenged destructive effects."""

from pathlib import Path

import pytest
from test_co_r01_lifecycle_architecture import _fixture

from oh_goon.repository_tooling.architecture.co_r01_lifecycle_contract import (
    check_co_r3t_no_legacy_pid_scavenging,
)

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "module,old,new",
    [
        (
            "_fleet_plan_cgroup_resources.py",
            'raise FleetPlanInvalid(\n            "Fleet legacy orphan lacks independent physical custody; refusing destructive PID scavenging"\n        )',
            "_kill_cgroup(cgroup, expected_identity=(device, inode))",
        ),
        (
            "_fleet_plan_restricted_egress.py",
            'raise FleetPlanInvalid(\n            "Fleet legacy network orphan lacks independent physical custody; refusing destructive PID scavenging"\n        )',
            "_cleanup_egress_resources(lease.ip, lease.nft, namespace, host_veth, table)",
        ),
    ],
)
def test_reintroducing_legacy_pid_scavenge_breaks_original_x0_guard(
    tmp_path: Path, module: str, old: str, new: str
) -> None:
    root = _fixture(tmp_path)
    assert check_co_r3t_no_legacy_pid_scavenging(root) == []
    copy = root / "backend/src/oh_goon/integrations" / module
    source = copy.read_text()
    assert old in source
    copy.write_text(source.replace(old, new, 1))
    errors = check_co_r3t_no_legacy_pid_scavenging(root)
    assert any(error.path == copy for error in errors)


def test_original_legacy_owners_fail_closed() -> None:
    assert check_co_r3t_no_legacy_pid_scavenging(ROOT) == []
