"""Pin the original LACE gate against mount-probe fail-open regression."""

from __future__ import annotations

from pathlib import Path

import pytest

from oh_goon.repository_tooling.architecture.x0_single_execution_contract import (
    _check_co_r3u2b1_original_launch_effect_cuts,
)

ROOT = Path(__file__).resolve().parents[2] / "backend/src/oh_goon"
SOURCES = (
    "integrations/_fleet_plan_cgroup_resources.py",
    "integrations/_fleet_plan_nested_cgroup_launcher.py",
    "integrations/_fleet_plan_restricted_egress.py",
)


def test_canonical_unconditional_original_gate() -> None:
    assert _check_co_r3u2b1_original_launch_effect_cuts(ROOT) == []


@pytest.mark.parametrize(
    "mutation",
    [
        "if _is_cgroup2_path(cgroup):\n        require_independent_native_parent_custody(operation=operation)",
        "if cgroup.exists():\n        require_independent_native_parent_custody(operation=operation)",
        "if False:\n        require_independent_native_parent_custody(operation=operation)",
    ],
)
def test_cgroup_mount_shortcut_cannot_pass_architecture(
    tmp_path: Path, mutation: str
) -> None:
    package = tmp_path / "oh_goon"
    for source in SOURCES:
        target = package / source
        target.parent.mkdir(parents=True, exist_ok=True)
        content = (ROOT / source).read_text(encoding="utf-8")
        if source.endswith("_fleet_plan_cgroup_resources.py"):
            content = content.replace(
                "    require_independent_native_parent_custody(operation=operation)\n\n\ndef delegated_process_subgroup",
                "    " + mutation + "\n\n\ndef delegated_process_subgroup",
                1,
            )
            assert content != (ROOT / source).read_text(encoding="utf-8")
        target.write_text(content, encoding="utf-8")
    assert _check_co_r3u2b1_original_launch_effect_cuts(package)
