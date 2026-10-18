"""M194: closed native Run contract cannot silently become a replay issuer.

This is an executable *negative* cutover audit, not replay enablement.
"""
from __future__ import annotations

import pytest

from oh_goon.core.enforcer_lace_trust import (
    AdmittedRunContract,
    TrustProtocolError,
    admission_receipt,
)
from oh_goon.services.fleet_admission import FleetAdmissionPlanner
from tests.unit.core.test_enforcer_lace_trust import authority, authenticator
from tests.unit.services.test_group_a_m191_native_replay_capture_fence import _case
from tests.unit.services.test_group_a_atomic_fleet_prework_ingress import prepared, publish, facts


@pytest.mark.parametrize("field", [
    "parent_run_id", "readmitted_from_run_id", "rerun_from_run_id",
])
def test_replay_parent_is_not_an_admitted_run_contract_field(field):
    """A caller cannot smuggle a new lineage authority into a signed contract."""
    fields = authority(run_id="child")
    fields[field] = "parent"
    with pytest.raises(TrustProtocolError, match="incomplete or unknown"):
        AdmittedRunContract.freeze(fields)


def test_valid_child_receipt_does_not_assert_parent_lineage():
    """Authenticated ordinary admission identity is not parent-child authorization."""
    contract = AdmittedRunContract.freeze(authority(run_id="child"))
    auth = authenticator()
    receipt = admission_receipt(auth, contract)
    claims = auth.verify(receipt, domain="admission")
    assert claims["run_id"] == "child"
    assert claims["contract_identity"] == contract.identity
    assert not ({"parent_run_id", "readmitted_from_run_id", "rerun_from_run_id"} & set(claims))


@pytest.mark.parametrize("kind", ["readmission", "rerun"])
def test_signed_deployment_policy_claim_does_not_enable_native_replay(
    tmp_path, monkeypatch, kind,
):
    """Even an authenticated opaque policy claim cannot promote native lineage."""
    args, connection, native_calls = prepared(tmp_path, monkeypatch)
    _, journal, repository_scope, targets, intent = _case(
        tmp_path / "replay-parent", kind=kind,
    )
    fields = args["contract"].fields
    parent_field = ("readmitted_from_run_id" if kind == "readmission"
                    else "rerun_from_run_id")
    fields["lineage_policy"] = {parent_field: "parent"}
    args["contract"] = AdmittedRunContract.freeze(fields)
    args["intent"] = intent
    args["repository_scope"] = repository_scope
    args["targets"] = list(targets)
    try:
        assert facts(args, connection) == (None, 0, 0)
        assert journal.read_view().required_run_detail("parent") is not None
        with pytest.raises(TrustProtocolError, match="authenticated replay.*native"):
            publish(args)
        assert native_calls == []
        assert facts(args, connection) == (None, 0, 0)
        assert journal.read_view().run_detail("child") is None
        assert args["signer_authority"].active() is not None
    finally:
        connection.close()


def test_non_replay_native_atomic_publication_still_works(tmp_path, monkeypatch):
    args, connection, calls = prepared(tmp_path, monkeypatch)
    try:
        FleetAdmissionPlanner.require_fresh_native_admission(args["intent"])
        assert facts(args, connection) == (None, 0, 0)
        assert publish(args) is not None
        assert set(calls) == set(args["trusted_public_roots"])
        assert facts(args, connection)[1:] == (1, 2)
    finally:
        connection.close()
