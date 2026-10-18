"""A reproducible native gate bypass is rejected by the original X0 guard."""
from pathlib import Path

import pytest

from oh_goon.repository_tooling.architecture.x0_single_execution_contract import (
    _check_co_r3u1_native_custody_fail_before_effect,
)

ROOT = Path(__file__).resolve().parents[4]


@pytest.mark.parametrize('owner,removed', [
    ('integrations/_fleet_plan_cgroup_resources.py',
     'require_independent_native_parent_custody(operation="native cgroup admission")'),
    ('integrations/_fleet_plan_cgroup_resources.py',
     '_refuse_unprovisioned_kernel_effect(cgroup, operation="native cgroup removal")'),
    ('integrations/fleet_plan_local_lace_native_reaper.py',
     'require_independent_native_parent_custody('),
])
def test_removing_original_effect_fence_fails_x0(
    tmp_path: Path, owner: str, removed: str,
) -> None:
    destination = tmp_path / 'backend/src/oh_goon'
    for relative in ('integrations/_fleet_plan_cgroup_resources.py',
                     'integrations/fleet_plan_local_lace_native_reaper.py',
                     'core/models/original_native_custody.py'):
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text((ROOT / 'backend/src/oh_goon' / relative).read_text())
    target = destination / owner
    contents = target.read_text()
    assert removed in contents
    target.write_text(contents.replace(removed, '# CO-R3U1 mutation', 1))
    assert _check_co_r3u1_native_custody_fail_before_effect(destination)


def test_original_x0_native_custody_owner_is_present() -> None:
    assert _check_co_r3u1_native_custody_fail_before_effect(ROOT / 'backend/src/oh_goon') == []
