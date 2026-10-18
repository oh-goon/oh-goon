"""Prevent original LACE host/generation admission from becoming another permit."""
from __future__ import annotations

from pathlib import Path

import pytest

from oh_goon.repository_tooling.architecture.x0_single_execution_contract import (
    _check_co_r3u2b2b1_host_effect_preconditions,
)

ROOT = Path(__file__).resolve().parents[4] / 'backend/src/oh_goon'
FILES = (
    'integrations/_fleet_plan_cgroup_resources.py',
    'integrations/fleet_plan_bubblewrap_isolation.py',
)


def test_original_host_effect_preconditions_remain_canonical() -> None:
    assert _check_co_r3u2b2b1_host_effect_preconditions(ROOT) == []


@pytest.mark.parametrize('file,original,replacement', [
    (FILES[0], 'filesystem_flags & getattr(os, "ST_RDONLY", 1)', 'False'),
    (FILES[0], 'filesystem_flags = os.statvfs(root).f_flag', 'filesystem_flags = 0'),
    (FILES[0], 'if requirement is not None and not requirement.complete_scope:', 'if False:'),
    (FILES[1], '            require_effect_authority()\n            resource_lease = CgroupV2AttemptLease.create(', '            resource_lease = CgroupV2AttemptLease.create('),
    (FILES[1], '            require_effect_authority()\n            network_lease = RestrictedEgressLease.create(', '            network_lease = RestrictedEgressLease.create('),
])
def test_removing_original_host_or_epoch_boundary_fails_x0(
    tmp_path: Path, file: str, original: str, replacement: str,
) -> None:
    package = tmp_path / 'oh_goon'
    for src in FILES:
        dest = package / src
        dest.parent.mkdir(parents=True, exist_ok=True)
        content = (ROOT / src).read_text(encoding='utf-8')
        if src == file:
            assert original in content
            content = content.replace(original, replacement, 1)
        dest.write_text(content, encoding='utf-8')
    assert _check_co_r3u2b2b1_host_effect_preconditions(package)
