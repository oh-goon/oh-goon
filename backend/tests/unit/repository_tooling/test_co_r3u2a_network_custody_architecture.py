"""CO-R3U2A X0 mutation checks: no parallel permit or effect bypass."""

from __future__ import annotations

from pathlib import Path

import pytest

from oh_goon.repository_tooling.architecture.x0_single_execution_contract import (
    _check_co_r3u2a_original_egress_custody,
)

ROOT = Path(__file__).resolve().parents[4]
OWNER = "integrations/_fleet_plan_restricted_egress.py"


@pytest.mark.parametrize(
    "cut",
    [
        'require_independent_native_parent_custody(operation="native restricted-egress admission")',
        'require_independent_native_parent_custody(operation="native restricted-egress allocation")',
        'require_independent_native_parent_custody(operation="native restricted-egress link creation")',
        'require_independent_native_parent_custody(operation="native restricted-egress setup command")',
        'require_independent_native_parent_custody(operation="native restricted-egress teardown")',
        'require_independent_native_parent_custody(operation="native restricted-egress pending record")',
        'require_independent_native_parent_custody(operation="native restricted-egress removal")',
    ],
)
def test_removing_a_native_cut_is_rejected(tmp_path: Path, cut: str) -> None:
    directory = tmp_path / "backend/src/oh_goon"
    target = directory / OWNER
    target.parent.mkdir(parents=True)
    original = (ROOT / "backend/src/oh_goon" / OWNER).read_text(encoding="utf-8")
    assert cut in original
    target.write_text(original.replace(cut, "None  # malicious custody bypass", 1))
    assert _check_co_r3u2a_original_egress_custody(directory)


def test_replacing_original_gate_import_with_local_permit_is_rejected(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "backend/src/oh_goon"
    target = directory / OWNER
    target.parent.mkdir(parents=True)
    original = (ROOT / "backend/src/oh_goon" / OWNER).read_text(encoding="utf-8")
    target.write_text(
        original.replace(
            "from oh_goon.integrations._fleet_plan_cgroup_resources import require_independent_native_parent_custody",
            "def require_independent_native_parent_custody(**kwargs): pass",
        )
    )
    assert _check_co_r3u2a_original_egress_custody(directory)


def test_original_network_guard_matches_current_source() -> None:
    assert (
        _check_co_r3u2a_original_egress_custody(
            ROOT / "backend/src/oh_goon",
        )
        == []
    )
