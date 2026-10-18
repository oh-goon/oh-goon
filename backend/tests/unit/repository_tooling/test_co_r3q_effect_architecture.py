"""CO-R3Q original execution-edge mutation regression."""
from __future__ import annotations
from pathlib import Path
import ast

from oh_goon.repository_tooling.architecture.co_r01_lifecycle_contract import _method


def _edges(source: str) -> tuple[int, int, int]:
    tree = ast.parse(source)
    execute = _method(tree, 'BubblewrapProcessIsolation', 'execute')
    assert execute is not None
    text = ast.unparse(execute)
    allocation = text.count('require_effect_authority()\n        resource_lease.open()')
    network = text.count('require_effect_authority()\n                network_lease.open(')
    launch = text.count('require_effect_authority()\n                result = run_command(')
    return allocation, network, launch


def test_mutations_dropping_each_original_effect_fence_are_rejected():
    root = Path(__file__).resolve().parents[3]
    source = (root / 'src/oh_goon/integrations/fleet_plan_bubblewrap_isolation.py').read_text()
    assert _edges(source) == (1, 1, 1)
    for before, after in (
        ('require_effect_authority()\n            resource_lease.open()', 'resource_lease.open()'),
        ('require_effect_authority()\n                    network_lease.open(', 'network_lease.open('),
        ('require_effect_authority()\n                    result = run_command(', 'result = run_command('),
    ):
        assert before in source
        assert _edges(source.replace(before, after, 1)) != (1, 1, 1)
