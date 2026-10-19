"""M192: native replay cannot reach original reservation or receipt publication.

The Journal can validate legacy lineage, but it is not an authenticated replay
issuer. This tests the original FleetAdmissionPlanner entrypoints, not an
alternate wrapper or invented replay authorization.
"""

from __future__ import annotations

import pytest

from oh_goon.core.enforcer_lace_trust import TrustProtocolError
from oh_goon.services.fleet_admission import FleetAdmissionPlanner
from tests.unit.services.test_group_a_atomic_fleet_prework_ingress import (
    facts,
    prepared,
    publish,
)
from tests.unit.services.test_group_a_fleet_reservation_transaction_bridge import _bridge
from tests.unit.services.test_group_a_m191_native_replay_capture_fence import _case


def _rerun(intent):
    return intent.model_copy(
        update={
            "execution": intent.execution.model_copy(
                update={
                    "rerun_from_run_id": "historical-parent",
                }
            ),
        }
    )


@pytest.mark.parametrize("kind", ["rerun", "readmission"])
def test_direct_atomic_publication_denies_replay_before_receipt_or_native_observation(
    tmp_path,
    monkeypatch,
    kind,
):
    args, conn, observations = prepared(tmp_path, monkeypatch)
    if kind == "rerun":
        args["intent"] = _rerun(args["intent"])
    else:
        _, _, _, _, child = _case(tmp_path / "source", kind="readmission")
        args["intent"] = child
    from oh_goon.core import enforcer_lace_trust

    receipts = []

    def forbidden_receipt(*a, **kw):
        receipts.append(True)
        raise AssertionError("receipt must not be produced for replay")

    monkeypatch.setattr(enforcer_lace_trust, "admission_receipt", forbidden_receipt)
    try:
        with pytest.raises(TrustProtocolError, match="authenticated replay.*native"):
            publish(args)
        assert receipts == []
        assert observations == []
        assert facts(args, conn) == (None, 0, 0)
    finally:
        conn.close()


@pytest.mark.parametrize("kind", ["rerun", "readmission"])
def test_original_reservation_denies_replay_before_any_lease_or_issuer(
    tmp_path,
    monkeypatch,
    kind,
):
    args, params, conn, observations = _bridge(tmp_path, monkeypatch)
    if kind == "rerun":
        params["intent"] = _rerun(params["intent"])
    else:
        _, _, _, _, child = _case(tmp_path / "source", kind="readmission")
        params["intent"] = child
    calls = []

    def forbidden_reserve(*a, **kw):
        calls.append(True)
        raise AssertionError("replay must not reserve execution")

    monkeypatch.setattr(params["execution"], "reserve_fleet_with", forbidden_reserve)
    try:
        with pytest.raises(TrustProtocolError, match="authenticated replay.*native"):
            FleetAdmissionPlanner.reserve_atomic_fleet_prework_ingress(**params)
        assert calls == []
        assert observations == []
        assert conn.execute("SELECT count(*) FROM execution_lease").fetchone()[0] == 0
        assert facts(args, conn) == (None, 0, 0)
    finally:
        conn.close()


@pytest.mark.parametrize(
    "entry", ["reserve_atomic_fleet_policy_ingress", "publish_compiled_strict_fleet_in"]
)
def test_strict_policy_atomic_entry_denies_lineage_before_owner_resolution(
    tmp_path,
    entry,
):
    _, journal, scope, repositories, child = _case(tmp_path, kind="readmission")
    method = getattr(FleetAdmissionPlanner, entry)
    # Every subsequent actor is deliberately absent: the replay barrier must
    # precede lease lookup, placement checks, compilation and native issuance.
    if entry == "reserve_atomic_fleet_policy_ingress":
        arguments = dict(
            execution=None,
            expected_durable_revision=0,
            operational_journal=journal,
            run_id="child",
            deployment_policy=None,
            authenticator=None,
            configured=None,
            current=None,
            intent=child,
            repository_scope=scope,
            targets=list(repositories),
            path_authority=None,
            key_directory=None,
            trusted_public_roots=None,
        )
    else:
        arguments = dict(
            connection=None,
            execution=None,
            operational_journal=journal,
            binding=None,
            intent=child,
            repository_scope=scope,
            targets=list(repositories),
            fleet_run_id="child",
        )
    with pytest.raises(TrustProtocolError, match="authenticated replay.*native"):
        method(**arguments)
    assert journal.read_view().run_detail("child") is None
    assert journal.read_view().required_run_detail("parent").run.fleet_run_id == "parent"
