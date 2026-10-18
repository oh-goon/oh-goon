"""SKILL-3C2D2C2C: original RESOURCE_INTENT cannot lose signed mount identity.

No native execution, Agent permission or physical containment is exercised here.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.integrations.fleet_plan_bubblewrap_isolation import _prepare_mounts
from oh_goon.integrations.fleet_plan_local_lace_intent import local_resource_identities
from tests.unit.integrations.test_b12c_live_prework_resource_bridge import _request


def test_original_local_intent_rejects_missing_signed_mount_identity_before_runtime_work(
    tmp_path, monkeypatch,
):
    request = _request(tmp_path)
    request.local_resource_intent = SimpleNamespace()

    from oh_goon.integrations import fleet_plan_bubblewrap_isolation as native
    reached = []
    monkeypatch.setattr(native, 'verify_runtime_root', lambda _: reached.append('runtime'))
    with pytest.raises(FleetPlanInvalid, match='signed RESOURCE_INTENT mount identity required'):
        _prepare_mounts('/bin/true', request, atomic_authority=object(), network_prefix=())
    assert reached == []


def test_original_local_intent_rejects_forged_signed_mount_identity_before_runtime_work(
    tmp_path, monkeypatch,
):
    request = _request(tmp_path)
    request.local_resource_intent = SimpleNamespace()
    from oh_goon.integrations import fleet_plan_bubblewrap_isolation as native
    reached = []
    monkeypatch.setattr(native, 'verify_runtime_root', lambda _: reached.append('runtime'))
    with pytest.raises(FleetPlanInvalid, match='sources differ from original signed'):
        _prepare_mounts('/bin/true', request, atomic_authority=object(),
                        network_prefix=(), signed_mounts_identity='sha256:' + '0'*64)
    assert reached == []


def test_original_mount_identity_can_still_be_verified_without_native_allocation(tmp_path):
    request = _request(tmp_path)
    identity = local_resource_identities(request)['mounts']
    from oh_goon.integrations.fleet_plan_bubblewrap_isolation import _require_original_signed_mount_identity
    _require_original_signed_mount_identity(request, identity)
