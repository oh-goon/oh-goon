"""M193: positive legacy lineage does NOT authorize authenticated native replay.

Use the original Journal and original native admission owner. Historical positive
lineage is deliberately not counted as positive issuer/signer/lease proof.
"""
from __future__ import annotations

import pytest

from oh_goon.core.enforcer_lace_trust import TrustProtocolError
from oh_goon.repository_tooling.enforcer_lace_prework_capture import PreworkRunCapture
from oh_goon.services.fleet_admission import FleetAdmissionPlanner
from tests.factories.journal import admit_run
from tests.unit.services.test_group_a_m191_native_replay_capture_fence import _case
from tests.unit.services.test_group_a_atomic_fleet_prework_ingress import prepared, publish, facts


def test_real_sqlite_accepts_historical_lineage_candidate_but_not_native_capture(tmp_path, monkeypatch):
    db, journal, scope, targets, child = _case(tmp_path, kind="readmission")
    calls = []
    prepare = journal.prepare_run_admission_in

    def observed_prepare(*args, **kwargs):
        calls.append("journal_candidate")
        return prepare(*args, **kwargs)

    monkeypatch.setattr(journal, "prepare_run_admission_in", observed_prepare)
    with db.transaction(write=True) as connection:
        before = connection.total_changes
        # A real positive historic parent->child Journal projection exists.
        candidate = journal.prepare_run_admission_in(
            connection, "child", child, scope, list(targets),
        )
        assert candidate.fleet_run_id == "child"
        assert connection.total_changes == before
        calls.clear()
        # Yet the original native publisher MUST fail earlier than even that
        # valid Journal candidate. This is the missing positive native bridge.
        with pytest.raises(TrustProtocolError, match="authenticated replay.*native"):
            FleetAdmissionPlanner.prepare_fleet_prework_capture_in(
                journal=journal, connection=connection, run_id="child",
                intent=child, repository_scope=scope, targets=list(targets),
            )
        assert calls == []
        assert connection.total_changes == before
        assert journal.read_view_from_connection(connection).run_detail("child") is None
    assert journal.read_view().run_detail("child") is None
    assert journal.read_view().required_run_detail("parent").run.fleet_run_id == "parent"


def test_legacy_positive_child_is_not_an_authenticated_native_issuer(tmp_path):
    _, journal, scope, targets, child = _case(tmp_path, kind="readmission")
    # Historical operational Journal can legitimately record this child.
    admitted = admit_run(journal, "child", child, scope, list(targets))
    assert admitted.fleet_run_id == "child"
    assert journal.read_view().readmission_child("parent") is not None
    # That durable record is not a trusted six-domain native issuer grant.
    with pytest.raises(TrustProtocolError, match="authenticated replay.*native"):
        FleetAdmissionPlanner.require_fresh_native_admission(child)


@pytest.mark.parametrize("kind", ["readmission", "rerun"])
def test_valid_signer_and_reservation_do_not_license_historical_replay(
    tmp_path, monkeypatch, kind,
):
    # A valid ordinary Run contract, signer trust epoch and held native lease
    # are available. Replay still must be refused before new issuer work.
    args, connection, native_calls = prepared(tmp_path, monkeypatch)
    _, _, scope, targets, child = _case(tmp_path / "replay-history", kind=kind)
    args["intent"] = child
    args["repository_scope"] = scope
    args["targets"] = list(targets)
    try:
        assert facts(args, connection) == (None, 0, 0)
        with pytest.raises(TrustProtocolError, match="authenticated replay.*native"):
            publish(args)
        assert native_calls == []
        assert facts(args, connection) == (None, 0, 0)
        assert args["signer_authority"].active() is not None
    finally:
        connection.close()


def test_original_non_replay_atomic_positive_control_is_retained(tmp_path, monkeypatch):
    # Existing signer stubs exercise the *ordinary* six-evidence atomic cut.
    # Do not misrepresent test signer stubs as native positive replay proof.
    args, connection, issued = prepared(tmp_path, monkeypatch)
    try:
        assert facts(args, connection) == (None, 0, 0)
        result = publish(args)
        assert result is not None
        assert set(issued) == set(args["trusted_public_roots"])
        assert facts(args, connection)[1:] == (1, 2)
    finally:
        connection.close()
