"""M158 tests: independently signed scope comes from committed Run, not caller."""
from __future__ import annotations

from pathlib import Path

import pytest

from oh_goon.core.enforcer_lace_trust import AdmittedRunContract, TrustProtocolError
from oh_goon.core.models.fleet_plan_evidence import PlanSnapshotEvidence
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.run_admission import (
    DirectFleetExecutionAdmission, FleetRunAdmissionIntent,
    PlanRepositoryAdmissionEvidence, fleet_repository_scope,
)
from oh_goon.core.models.source_authority import RepositorySourceAuthority
from oh_goon.core.vocabulary import OperationAction
from oh_goon.integrations.content_snapshot import SnapshotIdentity
from oh_goon.repository_tooling import enforcer_lace_repository_snapshot_issuer as native
from oh_goon.repository_tooling import enforcer_lace_captured_run_issuer as issuer
from oh_goon.repository_tooling import enforcer_lace_run_source_issuer as multi
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.journal import Journal
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
from tests.factories.capabilities import capability_snapshot
from tests.factories.fleet_plan_targets import structural_fleet_plan_target
from tests.factories.journal import admit_run, start_run
from tests.unit.services.test_group_a_verified_ingress_authorities import _test_private_key, roots, setup


def prepared(tmp_path: Path, monkeypatch):
    plan_path = tmp_path / "goon.yml"
    plan_path.write_text("version: 1\nexecution:\n  purpose: verification\nsteps:\n  - id: check\n    run: 'true'\n")
    plan = FleetPlanResolver().resolve(plan_path)
    target = structural_fleet_plan_target(plan)
    identities = []
    admissions = []
    observed = {}
    for ch in "abc":
        root = tmp_path / f"repo-{ch}"
        root.mkdir()
        (root / "code.txt").write_text(ch)
        repo = RepositoryIdentity(path=root)
        identities.append(repo)
        subject = "repo-subject-instance-" + ch * 64
        snapshot = SnapshotIdentity(ch * 64, 1, 1)
        authority = RepositorySourceAuthority.create(
            subject_instance_id=subject,
            source_snapshot=PlanSnapshotEvidence(sha256=snapshot.sha256, byte_count=1, entry_count=1),
        )
        admissions.append(PlanRepositoryAdmissionEvidence.create(
            repository_id=repo.repository_id, plan_id=plan.plan_id,
            semantic_preview_id="fleet-plan-repository-preview-" + ch * 64,
            source_authority=authority,
        ))
        observed[root] = (subject, snapshot)
    admissions.sort(key=lambda x: x.repository_id)
    identities.sort(key=lambda x: x.repository_id)
    monkeypatch.setattr(native, "repository_subject_instance_id", lambda p: observed[p][0])
    monkeypatch.setattr(native, "source_snapshot_identity", lambda p: observed[p][1])
    journal_path = tmp_path / "db.sqlite3"
    journal = Journal(LocalDatabase(journal_path))
    intent = FleetRunAdmissionIntent(
        target=target, capability=capability_snapshot(OperationAction.EXECUTE_PLAN, target),
        execution=DirectFleetExecutionAdmission(), repository_admissions=tuple(admissions),
    )
    admitted = admit_run(journal, "durable-run", intent,
                        fleet_repository_scope(set(x.repository_id for x in identities)), identities)
    assert admitted.fleet_run_id == "durable-run"
    _db, _causal, _env, original_contract, _keys, _ = setup()
    fields = original_contract.fields
    fields["run_id"] = "durable-run"
    original_admitted = journal.read_view().run_events("durable-run").events[0]
    fields["repository_identity"] = issuer.admitted_run_repository_capture_identity(original_admitted)
    fields["source_identity"] = multi.admitted_run_repository_source_identity(
        admissions=tuple(admissions),
        selected_repository_ids=tuple(a.repository_id for a in admissions),
    )
    contract = AdmittedRunContract.freeze(fields)
    return journal, journal_path, contract, identities, observed


def issue(journal, contract):
    return issuer.issue_observed_captured_run_source(
        journal=journal, contract=contract,
        issuer_id=roots()["source_identity"][0],
        signing_key=_test_private_key("source_identity"),
    )


def test_issuer_reads_only_durable_captured_scope(monkeypatch, tmp_path):
    journal, path, contract, identities, _ = prepared(tmp_path, monkeypatch)
    signed = issue(journal, contract)
    assert signed.field == "source_identity"
    reopened = Journal(LocalDatabase(path))
    assert issue(reopened, contract) == signed
    assert len(reopened.read_view().run_events("durable-run").events) == 1


def test_nonexistent_or_wrong_run_rejected(monkeypatch, tmp_path):
    journal, _, contract, _, _ = prepared(tmp_path, monkeypatch)
    fields = contract.fields
    fields["run_id"] = "missing"
    with pytest.raises(TrustProtocolError, match="unavailable"):
        issue(journal, AdmittedRunContract.freeze(fields))


def test_source_identity_from_subset_or_other_run_rejected(monkeypatch, tmp_path):
    journal, _, contract, _, _ = prepared(tmp_path, monkeypatch)
    fields = contract.fields
    fields["source_identity"] = "sha256:" + "0" * 64
    with pytest.raises(TrustProtocolError, match="contract source identity differs"):
        issue(journal, AdmittedRunContract.freeze(fields))


def test_started_run_is_not_eligible_for_late_signature(monkeypatch, tmp_path):
    journal, _, contract, _, _ = prepared(tmp_path, monkeypatch)
    start_run(journal, "durable-run")
    with pytest.raises(TrustProtocolError, match="sole committed admission"):
        issue(journal, contract)


def test_journal_changed_during_native_observation_rejected(monkeypatch, tmp_path):
    journal, _, contract, _, observed = prepared(tmp_path, monkeypatch)
    original = native.source_snapshot_identity
    fired = False
    def observe(root):
        nonlocal fired
        if not fired:
            fired = True
            start_run(journal, "durable-run")
        return original(root)
    monkeypatch.setattr(native, "source_snapshot_identity", observe)
    with pytest.raises(TrustProtocolError, match="sole committed admission"):
        issue(journal, contract)
    assert fired


def test_native_member_drift_rejected(monkeypatch, tmp_path):
    journal, _, contract, _, observed = prepared(tmp_path, monkeypatch)
    roots_paths = list(observed)
    observed[roots_paths[0]] = ("repo-subject-instance-" + "f" * 64, observed[roots_paths[0]][1])
    with pytest.raises(TrustProtocolError, match="differ"):
        issue(journal, contract)


def test_missing_signer_rejected_before_native_work(monkeypatch, tmp_path):
    journal, _, contract, _, _ = prepared(tmp_path, monkeypatch)
    with pytest.raises(TrustProtocolError, match="independent signing"):
        issuer.issue_observed_captured_run_source(journal=journal, contract=contract,
            issuer_id=roots()["source_identity"][0], signing_key=None)


def issue_repository(journal, contract):
    return issuer.issue_observed_captured_run_repository_identity(
        journal=journal, contract=contract,
        issuer_id=roots()["repository_identity"][0],
        signing_key=_test_private_key("repository_identity"),
    )


def test_repo_identity_signs_independent_durable_member_scope(monkeypatch, tmp_path):
    journal, path, contract, _, _ = prepared(tmp_path, monkeypatch)
    proof = issue_repository(journal, contract)
    assert proof.field == "repository_identity"
    assert proof != issue(journal, contract)
    from oh_goon.core.enforcer_lace_issuer_evidence import TrustedIssuerVerifier
    from tests.unit.services.test_group_a_verified_ingress_authorities import FIELDS, proof as fake_other
    signed = [proof if f == "repository_identity" else
              issue(journal, contract) if f == "source_identity" else
              fake_other(f, contract) for f in FIELDS]
    TrustedIssuerVerifier(roots()).verify(contract, evidence=tuple(signed),
       control_epoch=1, lease_identity="lease", lease_is_current=lambda *_: True)
    assert issue_repository(Journal(LocalDatabase(path)), contract) == proof


def test_repo_identity_rejects_wrong_contract_field(monkeypatch, tmp_path):
    journal, _, contract, _, _ = prepared(tmp_path, monkeypatch)
    fields = contract.fields
    fields["repository_identity"] = "sha256:" + "0" * 64
    with pytest.raises(TrustProtocolError, match="Run repository identity differs"):
        issue_repository(journal, AdmittedRunContract.freeze(fields))


def test_repo_identity_rejects_subject_swap_and_lifecycle_race(monkeypatch, tmp_path):
    journal, _, contract, _, observed = prepared(tmp_path, monkeypatch)
    first = next(iter(observed))
    old = observed[first]
    observed[first] = ("repo-subject-instance-" + "f" * 64, old[1])
    with pytest.raises(TrustProtocolError, match="no longer matches"):
        issue_repository(journal, contract)
    observed[first] = old
    original = native.source_snapshot_identity
    calls = 0
    def observe(root):
        nonlocal calls
        calls += 1
        if calls == 5:
            start_run(journal, "durable-run")
        return original(root)
    monkeypatch.setattr(native, "source_snapshot_identity", observe)
    with pytest.raises(TrustProtocolError, match="sole committed admission"):
        issue_repository(journal, contract)
    assert calls >= 5


def test_repository_issuer_cannot_sign_with_wrong_public_key(monkeypatch, tmp_path):
    journal, _, contract, _, _ = prepared(tmp_path, monkeypatch)
    other_field_key = _test_private_key("source_identity")
    forged = issuer.issue_observed_captured_run_repository_identity(
        journal=journal, contract=contract,
        issuer_id=roots()["repository_identity"][0], signing_key=other_field_key)
    from oh_goon.core.enforcer_lace_issuer_evidence import TrustedIssuerVerifier
    from tests.unit.services.test_group_a_verified_ingress_authorities import FIELDS, proof as fake_other
    evidences = tuple(forged if f == "repository_identity" else fake_other(f, contract) for f in FIELDS)
    with pytest.raises(TrustProtocolError, match="authentication failed"):
        TrustedIssuerVerifier(roots()).verify(contract, evidence=evidences,
            control_epoch=1, lease_identity="lease", lease_is_current=lambda *_: True)
