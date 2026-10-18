"""X0 mutation controls for the existing original mount owner/call chain."""
from pathlib import Path

import pytest

from oh_goon.repository_tooling.architecture.x0_single_execution_contract import (
    _check_original_signed_mount_consumption,
)
from oh_goon.repository_tooling.architecture.repository_layout import ROOT


SOURCE = ROOT / 'backend/src/oh_goon'


def _owner_file(tmp_path, relative: str, source: str):
    target = tmp_path / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source, encoding='utf-8')


def test_original_signed_mount_consumption_is_one_x0_owner():
    assert _check_original_signed_mount_consumption(SOURCE) == []


@pytest.mark.parametrize('mutation', [
    'drop-pre-allocation', 'drop-builder', 'no-signed-proof', 'disable-identity-forwarding',
    'replace-identity-with-none', 'replace-builder-identity-with-none',
])
def test_mount_order_mutations_are_rejected(tmp_path, mutation):
    original_owner = (SOURCE / 'runtime/enforcer_lace_local_resource_intent.py').read_text()
    original_backend = (SOURCE / 'integrations/fleet_plan_bubblewrap_isolation.py').read_text()
    if mutation == 'drop-pre-allocation':
        original_backend = original_backend.replace(
            '            _require_original_signed_mount_identity(request, proof.mounts_identity)\n', '', 1)
    elif mutation == 'drop-builder':
        original_backend = original_backend.replace(
            '        _require_original_signed_mount_identity(request, signed_mounts_identity)\n', '', 1)
    elif mutation == 'no-signed-proof':
        original_owner = original_owner.replace(
            '            mounts_identity=original_resources["mounts"],\n', '', 1)
    elif mutation == 'disable-identity-forwarding':
        original_backend = original_backend.replace(
            '                        signed_mounts_identity=proof.mounts_identity if proof is not None else None,\n', '', 1)
    elif mutation == 'replace-identity-with-none':
        original_backend = original_backend.replace(
            'signed_mounts_identity=proof.mounts_identity if proof is not None else None,',
            'signed_mounts_identity=None,', 1)
    else:
        original_backend = original_backend.replace(
            '            signed_mounts_identity=signed_mounts_identity,\n',
            '            signed_mounts_identity=None,\n', 1)
    assert original_backend != (SOURCE / 'integrations/fleet_plan_bubblewrap_isolation.py').read_text() or original_owner != (SOURCE / 'runtime/enforcer_lace_local_resource_intent.py').read_text(), 'Mutation did not apply to current owner'
    _owner_file(tmp_path, 'runtime/enforcer_lace_local_resource_intent.py', original_owner)
    _owner_file(tmp_path, 'integrations/fleet_plan_bubblewrap_isolation.py', original_backend)
    assert _check_original_signed_mount_consumption(tmp_path)
