"""B9 negative custody proof. All positive mock data is TEST ONLY.

The deployed owner alone can establish independently owned public roots.
Neither these mocks nor the original test signer qualify production provisioning.
"""

from __future__ import annotations

import os

import pytest

from oh_goon.core.enforcer_lace_trust import TrustProtocolError
from tests.unit.runtime.test_b12d2c3b8_original_issuer_restart_fence import (
    _snapshot,
    _transfer,
    owner,  # noqa: F401 - dependent test fixture
)


@pytest.mark.parametrize(
    "attack",
    [
        "missing_owner",
        "same_uid",
        "observer_reject",
        "insufficient_roots",
        "wrong_run",
        "foreign_schema",
        "mismatched_owner",
        "changed_signer_uid",
        "fake_attestation",
    ],
)
def test_absent_independent_root_custody_blocks_before_transfer(owner, attack):  # noqa: F811 - pytest fixture injection
    conn, journal, service, provisioner, *_ = owner
    if attack == "missing_owner":
        provisioner.public_root_owner_uid = None
    elif attack == "same_uid":
        provisioner.public_root_owner_uid = os.geteuid()
    else:
        provisioner.custody_attack = attack
    before = _snapshot(conn)
    with pytest.raises(TrustProtocolError):
        _transfer(service)
    assert _snapshot(conn) == before
    assert journal._row("run-one")[1:3] == (1, "lease-one")


def test_independent_custody_observed_before_and_after_transfer(owner):  # noqa: F811 - pytest fixture injection
    conn, _, service, provisioner, *_ = owner
    assert provisioner.custody_calls == 0
    result = _transfer(service)
    assert result.recovery_only is True
    assert provisioner.custody_calls == 2


def test_custody_lost_after_provisional_transfer_rolls_back(owner, monkeypatch):  # noqa: F811 - pytest fixture injection
    conn, journal, service, provisioner, *_ = owner
    before = _snapshot(conn)
    from oh_goon.runtime import enforcer_lace_restart_transfer as transfer

    original = transfer.commit_original_restart_transfer_in

    def revoke_after_provisional(*args, **kwargs):
        ticket = original(*args, **kwargs)
        provisioner.custody_attack = "observer_reject"
        return ticket

    monkeypatch.setattr(transfer, "commit_original_restart_transfer_in", revoke_after_provisional)
    with pytest.raises(TrustProtocolError):
        _transfer(service)
    assert _snapshot(conn) == before
    assert journal._row("run-one")[1:3] == (1, "lease-one")
