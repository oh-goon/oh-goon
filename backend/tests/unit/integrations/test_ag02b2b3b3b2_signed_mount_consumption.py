"""Source/intent/consumption must agree in the ONE original LACE path.

These tests do not certify native Bubblewrap mounts or Agent execution.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.integrations.fleet_plan_bubblewrap_isolation import (
    _require_original_signed_mount_identity, _prepare_mounts,
)
from oh_goon.integrations.fleet_plan_local_lace_intent import local_resource_identities
from tests.unit.integrations.test_b12c_live_prework_resource_bridge import (
    _backend, _execute, _permit, _request,
)


def _bind(request, tmp_path, *, source='source', target='/opt/selected', sha='c'):
    request.attempt.sandbox_bindings = (
        SimpleNamespace(snapshot_path=tmp_path / source, sandbox_path=target,
                        identity=SimpleNamespace(sha256=sha * 64)),
    )


def test_original_signed_journal_mount_digest_reaches_reservation_proof(tmp_path, monkeypatch):
    backend, touched = _backend(monkeypatch)
    request = _request(tmp_path)
    _bind(request, tmp_path)
    connection, journal, permit = _permit(tmp_path, request, backend)
    proof = permit.reserve(request, backend)
    signed = journal.verify('run-one', auth=permit.authenticator)[-1]
    assert signed['kind'] == 'RESOURCE_INTENT'
    assert proof.mounts_identity == signed['payload']['resources']['mounts']
    assert proof.mounts_identity == local_resource_identities(request)['mounts']
    touched.assert_not_called()
    connection.close()


@pytest.mark.parametrize('drift', ['source','target','content','host-tool'])
def test_mount_substitution_after_signed_reservation_fails_before_allocation(tmp_path, monkeypatch, drift):
    backend, touched = _backend(monkeypatch)
    request = _request(tmp_path)
    _bind(request, tmp_path)
    connection, journal, permit = _permit(tmp_path, request, backend)

    class SwappingOriginalOwner:
        def reserve(self, request, backend):
            proof = permit.reserve(request, backend)
            if drift == 'source':
                _bind(request, tmp_path, source='foreign')
            elif drift == 'target':
                _bind(request, tmp_path, target='/opt/foreign')
            elif drift == 'content':
                _bind(request, tmp_path, sha='d')
            else:
                request.attempt.host_tools = (SimpleNamespace(
                    name='sh', execution_snapshot_path=tmp_path / 'sh',
                    execution_identity=SimpleNamespace(sha256='a' * 64)),)
            return proof

    request.local_resource_intent = SwappingOriginalOwner()
    with pytest.raises(FleetPlanInvalid, match='mount sources differ from original signed'):
        _execute(backend, request)
    touched.assert_not_called()
    assert journal.verify('run-one', auth=permit.authenticator)[-1]['kind'] == 'RESOURCE_INTENT'
    connection.close()


def test_consumption_cut_revalidates_signed_mount_identity(tmp_path, monkeypatch):
    request = _request(tmp_path)
    _bind(request, tmp_path)
    signed = local_resource_identities(request)['mounts']
    _require_original_signed_mount_identity(request, signed)
    _bind(request, tmp_path, target='/opt/rewritten')
    with pytest.raises(FleetPlanInvalid, match='mount sources differ'):
        _require_original_signed_mount_identity(request, signed)
    # This is at the start of the original Bubblewrap mount builder, BEFORE
    # runtime-root validation or any native command launch.
    with pytest.raises(FleetPlanInvalid, match='mount sources differ'):
        _prepare_mounts('/bin/true', request, atomic_authority=object(),
                        network_prefix=(), signed_mounts_identity=signed)


def test_untrusted_or_missing_signed_mount_identity_is_not_mount_authority(tmp_path):
    request = _request(tmp_path)
    with pytest.raises(FleetPlanInvalid, match='mount sources differ'):
        _require_original_signed_mount_identity(request, 'sha256:' + '0' * 64)
    with pytest.raises(FleetPlanInvalid, match='mount sources differ'):
        _require_original_signed_mount_identity(request, None)
