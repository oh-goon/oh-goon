"""M159: admitted Plan-selected lock proof delegates freshness only to native uv."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from oh_goon.core.enforcer_lace_issuer_evidence import TrustedIssuerVerifier
from oh_goon.core.enforcer_lace_trust import AdmittedRunContract, TrustProtocolError
from oh_goon.core.models.fleet_plan import ResolvedFleetPlan
from oh_goon.core.models.fleet_plan_requirements import UvProjectRequirement
from oh_goon.repository_tooling import enforcer_lace_native_lock_issuer as issuer
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
from tests.factories.journal import start_run
from tests.unit.services.test_group_a_verified_ingress_authorities import (
    FIELDS,
    _test_private_key,
    proof,
    roots,
)


def fixture(tmp_path: Path, monkeypatch):
    from oh_goon.core.models.fleet_executor import (
        FleetExecutorPlacement,
        PlanExecutorCapacityEvidence,
    )
    from oh_goon.core.models.fleet_plan_evidence import (
        PlanIsolationBackendEvidence,
        PlanSnapshotEvidence,
    )
    from oh_goon.core.models.fleet_plan_execution_policy import CicdExecutionPolicy
    from oh_goon.core.models.repository import RepositoryIdentity
    from oh_goon.core.models.run_admission import (
        FleetRunAdmissionIntent,
        PlanRepositoryAdmissionEvidence,
        StrictFleetExecutionAdmission,
        fleet_repository_scope,
    )
    from oh_goon.core.models.source_authority import RepositorySourceAuthority
    from oh_goon.core.vocabulary import OperationAction
    from oh_goon.integrations.content_snapshot import SnapshotIdentity
    from oh_goon.repository_tooling import enforcer_lace_repository_snapshot_issuer as source_native
    from oh_goon.runtime.database import LocalDatabase
    from oh_goon.runtime.journal import Journal
    from tests.factories.capabilities import capability_snapshot
    from tests.factories.fleet_plan_targets import structural_fleet_plan_target
    from tests.factories.journal import admit_run
    from tests.unit.services.test_group_a_verified_ingress_authorities import setup

    plan_path = tmp_path / "goon.yml"
    plan_path.write_text(
        "version: 1\nexecution:\n  purpose: verification\nsteps:\n  - id: check\n    run: 'true'\n"
    )
    plain = FleetPlanResolver().resolve(plan_path)
    plan = ResolvedFleetPlan.create(
        name=plain.name,
        description=plain.description,
        steps=plain.steps,
        execution=CicdExecutionPolicy(),
        requirements=(UvProjectRequirement(project=".", group="dev"),),
        imports=plain.imports,
        step_sources=plain.step_sources,
    )
    target = structural_fleet_plan_target(plan)
    identities, admissions, observed = [], [], {}
    for ch in "abc":
        root = tmp_path / f"repo-{ch}"
        root.mkdir()
        (root / "code.txt").write_text(ch)
        repository = RepositoryIdentity(path=root)
        identities.append(repository)
        subject = "repo-subject-instance-" + ch * 64
        snapshot = SnapshotIdentity(ch * 64, 1, 1)
        authority = RepositorySourceAuthority.create(
            subject_instance_id=subject,
            source_snapshot=PlanSnapshotEvidence(
                sha256=snapshot.sha256, byte_count=1, entry_count=1
            ),
        )
        admissions.append(
            PlanRepositoryAdmissionEvidence.create(
                repository_id=repository.repository_id,
                plan_id=plan.plan_id,
                semantic_preview_id="fleet-plan-repository-preview-" + ch * 64,
                source_authority=authority,
            )
        )
        observed[root] = (subject, snapshot)
        (root / "pyproject.toml").write_text(
            '[project]\nname = "m159-project"\nversion = "0.1.0"\nrequires-python = ">=3.11"\ndependencies = []\n'
        )
        subprocess.run(
            ("uv", "lock", "--project", str(root), "--offline"),
            check=True,
            capture_output=True,
            text=True,
        )
    identities.sort(key=lambda x: x.repository_id)
    admissions.sort(key=lambda x: x.repository_id)
    monkeypatch.setattr(source_native, "repository_subject_instance_id", lambda p: observed[p][0])
    monkeypatch.setattr(source_native, "source_snapshot_identity", lambda p: observed[p][1])
    journal = Journal(LocalDatabase(tmp_path / "run.sqlite3"))
    backend = PlanIsolationBackendEvidence(
        backend_id="test-sandbox",
        configuration_id="test-backend",
        kernel_boundary="virtual_machine",
        remote_execution=True,
        experimental=False,
    )
    capacity = PlanExecutorCapacityEvidence(
        fleet_run_id="durable-run",
        pool_id="test-pool",
        placement_policy="priority_rendezvous_fifo_v2",
        placement_rank=0,
        executor_priority=0,
        executor_id="test",
        executor_generation=1,
        source="local_verified",
        backend_configuration_id=backend.configuration_id,
        capacity_cpu_millis=2000,
        capacity_memory_bytes=2 * 1024**3,
        requested_parallelism=1,
        admitted_parallelism=1,
    )
    strict = StrictFleetExecutionAdmission(
        placement=FleetExecutorPlacement(
            isolation_backend=backend, executor_capacity=capacity, executor_trust_epoch=0
        )
    )
    intent = FleetRunAdmissionIntent(
        target=target,
        capability=capability_snapshot(OperationAction.EXECUTE_PLAN, target),
        execution=strict,
        repository_admissions=tuple(admissions),
    )
    admit_run(
        journal,
        "durable-run",
        intent,
        fleet_repository_scope(set(x.repository_id for x in identities)),
        identities,
    )
    _, _, _, base_contract, _, _ = setup()
    fields = base_contract.fields
    fields["run_id"] = "durable-run"
    fields["lock_identity"] = issuer.observed_admitted_run_lock_identity(
        journal=journal, run_id="durable-run"
    )
    return journal, AdmittedRunContract.freeze(fields), observed


def sign(journal, contract):
    return issuer.issue_observed_captured_run_lock_identity(
        journal=journal,
        contract=contract,
        issuer_id=roots()["lock_identity"][0],
        signing_key=_test_private_key("lock_identity"),
    )


def test_native_uv_positive_and_independent_ed25519_verification(tmp_path, monkeypatch):
    journal, contract, observed = fixture(tmp_path, monkeypatch)
    signed = sign(journal, contract)
    assert signed.field == "lock_identity"
    proofs = tuple(
        signed if field == "lock_identity" else proof(field, contract) for field in FIELDS
    )
    TrustedIssuerVerifier(roots()).verify(
        contract,
        evidence=proofs,
        control_epoch=1,
        lease_identity="lease",
        lease_is_current=lambda *_: True,
    )
    assert sign(journal, contract) == signed
    assert len(observed) == 3


def test_lock_mutation_and_stale_native_lock_rejected(tmp_path, monkeypatch):
    journal, contract, observed = fixture(tmp_path, monkeypatch)
    project = next(iter(observed))
    (project / "uv.lock").write_text("malicious")
    with pytest.raises(TrustProtocolError, match="native uv rejected|differs"):
        sign(journal, contract)


def test_intent_mutation_rejected(tmp_path, monkeypatch):
    journal, contract, observed = fixture(tmp_path, monkeypatch)
    project = next(iter(observed))
    (project / "pyproject.toml").write_text('[project]\nname="different"\nversion="0.2.0"\n')
    with pytest.raises(TrustProtocolError):
        sign(journal, contract)


def test_symlink_lock_rejected_before_native_check(tmp_path, monkeypatch):
    journal, contract, observed = fixture(tmp_path, monkeypatch)
    project = next(iter(observed))
    lock = project / "uv.lock"
    lock.rename(project / "original")
    lock.symlink_to(project / "original")
    original = issuer.native_uv_locked_project_issue

    def should_not_run(current):
        if current == project:
            raise AssertionError("native work must not start for untrusted lock")
        return original(current)

    monkeypatch.setattr(issuer, "native_uv_locked_project_issue", should_not_run)
    with pytest.raises(TrustProtocolError, match="regular non-symlink"):
        sign(journal, contract)


def test_hardlink_lock_rejected(tmp_path, monkeypatch):
    journal, contract, observed = fixture(tmp_path, monkeypatch)
    root = next(iter(observed))
    (root / "also.lock").hardlink_to(root / "uv.lock")
    with pytest.raises(TrustProtocolError, match="unique regular"):
        sign(journal, contract)


def test_wrong_contract_lock_identity_rejected(tmp_path, monkeypatch):
    journal, contract, _ = fixture(tmp_path, monkeypatch)
    fields = contract.fields
    fields["lock_identity"] = "sha256:" + "0" * 64
    with pytest.raises(TrustProtocolError, match="contract lock identity differs"):
        sign(journal, AdmittedRunContract.freeze(fields))


def test_native_uv_unavailable_fails_closed(tmp_path, monkeypatch):
    journal, contract, _ = fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(issuer, "native_uv_locked_project_issue", lambda _: "uv unavailable")
    with pytest.raises(TrustProtocolError, match="native uv rejected.*unavailable"):
        sign(journal, contract)


def test_native_uv_stale_after_intent_mutation_fails_without_lock_write(tmp_path, monkeypatch):
    journal, contract, observed = fixture(tmp_path, monkeypatch)
    root = next(iter(observed))
    before = (root / "uv.lock").read_bytes()
    (root / "pyproject.toml").write_text(
        '[project]\nname = "m159-project"\nversion = "0.1.0"\nrequires-python = ">=3.11"\ndependencies = ["unlikely-unique-dep-m159>=8"]\n'
    )
    with pytest.raises(TrustProtocolError, match="native uv rejected"):
        sign(journal, contract)
    assert (root / "uv.lock").read_bytes() == before


def test_durable_run_started_during_lock_check_rejected(tmp_path, monkeypatch):
    journal, contract, _ = fixture(tmp_path, monkeypatch)
    old_check = issuer.native_uv_locked_project_issue
    called = False

    def change_run(project):
        nonlocal called
        if not called:
            called = True
            start_run(journal, "durable-run")
        return old_check(project)

    monkeypatch.setattr(issuer, "native_uv_locked_project_issue", change_run)
    with pytest.raises(TrustProtocolError, match="sole committed admission"):
        sign(journal, contract)
    assert called


def test_lock_replaced_between_native_check_and_signature_rejected(tmp_path, monkeypatch):
    journal, contract, observed = fixture(tmp_path, monkeypatch)
    root = next(iter(observed))
    old_check = issuer.native_uv_locked_project_issue
    called = False

    def replace(project):
        nonlocal called
        result = old_check(project)
        if project == root and not called:
            called = True
            (root / "uv.lock").write_bytes((root / "uv.lock").read_bytes() + b"\n")
        return result

    monkeypatch.setattr(issuer, "native_uv_locked_project_issue", replace)
    with pytest.raises(TrustProtocolError, match="changed during issuance"):
        sign(journal, contract)
    assert called


def test_missing_signer_fails_before_native_checks(tmp_path, monkeypatch):
    journal, contract, _ = fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(
        issuer,
        "native_uv_locked_project_issue",
        lambda _: (_ for _ in ()).throw(AssertionError("native must not run")),
    )
    with pytest.raises(TrustProtocolError, match="independent native lock signing"):
        issuer.issue_observed_captured_run_lock_identity(
            journal=journal,
            contract=contract,
            issuer_id=roots()["lock_identity"][0],
            signing_key=None,
        )


def test_not_admitted_lock_project_fails_before_native_work(tmp_path, monkeypatch):
    # M158's durable Run has no UV requirement and cannot self-declare a lock.
    from tests.unit.services.test_group_a_captured_run_source_issuer import prepared as plain

    journal, _, contract, _, _ = plain(tmp_path, monkeypatch)
    with pytest.raises(TrustProtocolError, match="no selected native uv lock"):
        issuer.issue_observed_captured_run_lock_identity(
            journal=journal,
            contract=contract,
            issuer_id=roots()["lock_identity"][0],
            signing_key=_test_private_key("lock_identity"),
        )
