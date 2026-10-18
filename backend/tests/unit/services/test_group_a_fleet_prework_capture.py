"""M166: pre-commit Fleet Run capture must be the existing Journal's exact event.

These are NOT live issuer/cutover tests: M157–M165 still require a committed
operational Run and therefore cannot produce pre-admission native signatures.
"""
from __future__ import annotations

import sqlite3

import pytest

from oh_goon.core.errors import JournalConflict
from oh_goon.core.enforcer_lace_trust import TrustProtocolError
from oh_goon.core.models.run_admission import FleetRunAdmissionIntent
from oh_goon.repository_tooling import enforcer_lace_native_issuer_suite as native_suite
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.journal import Journal
from oh_goon.services.fleet_admission import FleetAdmissionPlanner
from tests.unit.services.test_group_a_native_tool_issuer import fixture as native_fixture
from tests.unit.services.test_group_a_verified_ingress_authorities import roots


def setup(tmp_path, monkeypatch):
    original_journal, contract, path_authority, _ = native_fixture(tmp_path, monkeypatch)
    existing = original_journal.read_view().run_events("durable-run").events[0]
    fresh = Journal(LocalDatabase(tmp_path / "m166-precommit.sqlite"))
    intent = FleetRunAdmissionIntent(
        source=existing.source, target=existing.target, capability=existing.capability,
        execution=existing.execution, repository_admissions=existing.repository_admissions,
        application_launch=existing.application_launch, scheduling=existing.scheduling,
    )
    return fresh, contract, path_authority, intent, existing


def capture(journal, conn, intent, event):
    return FleetAdmissionPlanner.prepare_fleet_prework_capture_in(
        journal=journal, connection=conn, run_id=event.fleet_run_id,
        intent=intent, repository_scope=event.repository_scope,
        targets=list(event.targets),
    )


def test_precommit_capture_is_identical_to_actual_canonical_operational_admission(tmp_path, monkeypatch):
    journal, _, _, intent, event = setup(tmp_path, monkeypatch)
    with journal._database.transaction(write=True) as conn:
        before = conn.total_changes
        candidate = capture(journal, conn, intent, event)
        assert candidate.event_type.value == "RUN_ADMITTED"
        assert candidate.sequence == 1
        assert candidate.repository_admissions == event.repository_admissions
        assert conn.total_changes == before
        assert journal.read_view_from_connection(conn).run_detail("durable-run") is None
        journal.admit_run_in(conn, event.fleet_run_id, intent,
                             event.repository_scope, list(event.targets))
        stored = journal.read_view_from_connection(conn).run_events("durable-run").events[0]
        assert candidate.model_dump(exclude={"generated_at"}) == stored.model_dump(exclude={"generated_at"})
    assert journal.read_view().run_detail("durable-run") is not None


def test_prework_capture_is_not_an_admission_and_does_not_persist(tmp_path, monkeypatch):
    journal, _, _, intent, event = setup(tmp_path, monkeypatch)
    with journal._database.transaction(write=True) as conn:
        candidate = capture(journal, conn, intent, event)
        assert candidate.fleet_run_id == "durable-run"
    assert journal.read_view().run_detail("durable-run") is None


def test_prework_requires_an_existing_transaction_and_real_journal(tmp_path, monkeypatch):
    journal, _, _, intent, event = setup(tmp_path, monkeypatch)
    with sqlite3.connect(":memory:", isolation_level=None) as disconnected:
        with pytest.raises(JournalConflict, match="existing SQLite transaction"):
            capture(journal, disconnected, intent, event)
    with journal._database.transaction(write=True) as conn:
        with pytest.raises(TrustProtocolError, match="canonical operational Fleet Journal"):
            FleetAdmissionPlanner.prepare_fleet_prework_capture_in(
                journal=object(), connection=conn, run_id=event.fleet_run_id,
                intent=intent, repository_scope=event.repository_scope, targets=list(event.targets),
            )
        assert journal.read_view_from_connection(conn).run_detail("durable-run") is None


@pytest.mark.parametrize("invalid", ["missing-repository", "duplicated-repository", "wrong-run"])
def test_invalid_capture_fails_before_any_run_fact(tmp_path, monkeypatch, invalid):
    journal, _, _, intent, event = setup(tmp_path, monkeypatch)
    targets = list(event.targets)
    run_id = event.fleet_run_id
    if invalid == "missing-repository":
        targets = targets[:1]
    elif invalid == "duplicated-repository":
        targets += targets[:1]
    else:
        run_id = "different-run"
    with journal._database.transaction(write=True) as conn:
        before = conn.total_changes
        with pytest.raises((ValueError, JournalConflict, TrustProtocolError)):
            FleetAdmissionPlanner.prepare_fleet_prework_capture_in(
                journal=journal, connection=conn, run_id=run_id, intent=intent,
                repository_scope=event.repository_scope, targets=targets,
            )
        assert conn.total_changes == before
        assert journal.read_view_from_connection(conn).run_detail("durable-run") is None


def test_capture_rejects_when_run_already_exists(tmp_path, monkeypatch):
    journal, _, _, intent, event = setup(tmp_path, monkeypatch)
    with journal._database.transaction(write=True) as conn:
        journal.admit_run_in(conn, event.fleet_run_id, intent, event.repository_scope, list(event.targets))
    with journal._database.transaction(write=True) as conn:
        before = conn.total_changes
        with pytest.raises(JournalConflict, match="already"):
            capture(journal, conn, intent, event)
        assert conn.total_changes == before


def test_committed_run_only_issuer_suite_fails_closed_for_new_prework_candidate(tmp_path, monkeypatch):
    journal, contract, authority, intent, event = setup(tmp_path, monkeypatch)
    with journal._database.transaction(write=True) as conn:
        candidate = capture(journal, conn, intent, event)
        assert candidate.fleet_run_id == contract.fields["run_id"]
        # M164's six native functions are incapable of signing an uncommitted
        # candidate. Connecting them as-is would create a circular admission.
        with pytest.raises(TrustProtocolError, match="durable Run admission"):
            native_suite.issue_fixed_native_admission_evidence(
                journal=journal, contract=contract, path_authority=authority,
                key_directory=tmp_path / "nonexistent-keys", trusted_public_roots=roots(),
            )
        assert journal.read_view_from_connection(conn).run_detail("durable-run") is None
