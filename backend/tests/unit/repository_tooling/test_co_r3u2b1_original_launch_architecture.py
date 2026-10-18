"""CO-R3U2B1 X0 guards original launch paths, not a new authority owner."""
from __future__ import annotations

from pathlib import Path

import pytest

from oh_goon.repository_tooling.architecture.x0_single_execution_contract import (
    _check_co_r3u2b1_original_launch_effect_cuts,
)

ROOT = Path(__file__).resolve().parents[4]
PACKAGE = ROOT / 'backend/src/oh_goon'


def test_real_original_cuts_match_canonical_source() -> None:
    assert _check_co_r3u2b1_original_launch_effect_cuts(PACKAGE) == []


@pytest.mark.parametrize(('file', 'needle'), [
    ('integrations/_fleet_plan_cgroup_resources.py', 'operation="native cgroup process launch"'),
    ('integrations/_fleet_plan_cgroup_resources.py', 'operation="native cgroup limit write"'),
    ('integrations/_fleet_plan_cgroup_resources.py', 'operation="native cgroup direct limit write"'),
    ('integrations/_fleet_plan_cgroup_resources.py', "raise RuntimeError('original LACE independent kernel custody NOT PROVEN before process attachment')"),
    ('integrations/_fleet_plan_nested_cgroup_launcher.py', 'operation="native nested cgroup process launch"'),
    ('integrations/_fleet_plan_nested_cgroup_launcher.py', 'operation="native monitor cgroup process launch"'),
    ('integrations/_fleet_plan_restricted_egress.py', 'operation="native restricted-egress process launch"'),
])
def test_removed_original_cut_fails_architecture(
    tmp_path: Path, file: str, needle: str,
) -> None:
    target = tmp_path / 'backend/src/oh_goon' / file
    target.parent.mkdir(parents=True, exist_ok=True)
    original = (PACKAGE / file).read_text(encoding='utf-8')
    assert needle in original
    target.write_text(original.replace(needle, "operation='native removed custody gate'", 1), encoding='utf-8')
    # Give the checker all of the owner sources, but preserve the malicious
    # mutated target. No newly built semantic parsing or native writer.
    for other in ('integrations/_fleet_plan_cgroup_resources.py',
                  'integrations/_fleet_plan_nested_cgroup_launcher.py',
                  'integrations/_fleet_plan_restricted_egress.py'):
        other_path = tmp_path / 'backend/src/oh_goon' / other
        if not other_path.exists():
            other_path.parent.mkdir(parents=True, exist_ok=True)
            other_path.write_text((PACKAGE / other).read_text(encoding='utf-8'), encoding='utf-8')
    assert _check_co_r3u2b1_original_launch_effect_cuts(tmp_path / 'backend/src/oh_goon')


def test_moving_gate_below_cgroup_limit_write_fails_architecture(tmp_path: Path) -> None:
    target = tmp_path/'backend/src/oh_goon/integrations/_fleet_plan_cgroup_resources.py'
    target.parent.mkdir(parents=True)
    original = (PACKAGE/'integrations/_fleet_plan_cgroup_resources.py').read_text(encoding='utf-8')
    statement = '    _refuse_unprovisioned_kernel_effect(path.parent, operation="native cgroup direct limit write")\n'
    assert statement in original
    mutated = original.replace(statement, '', 1).replace('    path.write_text(value, encoding="ascii")',
                          '    path.write_text(value, encoding="ascii")\n'+statement.rstrip(), 1)
    target.write_text(mutated, encoding='utf-8')
    for name in ('_fleet_plan_nested_cgroup_launcher.py', '_fleet_plan_restricted_egress.py'):
        (target.parent/name).write_text((PACKAGE/'integrations'/name).read_text(encoding='utf-8'))
    assert _check_co_r3u2b1_original_launch_effect_cuts(tmp_path/'backend/src/oh_goon')
