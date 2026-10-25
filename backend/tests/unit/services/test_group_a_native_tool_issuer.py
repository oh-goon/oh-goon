"""M160: durable Run-selected host-tool evidence, no alternative PATH authority."""

from pathlib import Path

import pytest

from oh_goon.core.enforcer_lace_issuer_evidence import TrustedIssuerVerifier
from oh_goon.core.enforcer_lace_trust import AdmittedRunContract, TrustProtocolError
from oh_goon.core.path_authority import StrictHostPathAuthority
from oh_goon.integrations import content_snapshot
from oh_goon.integrations.content_snapshot import SnapshotIdentity
from oh_goon.repository_tooling import enforcer_lace_native_tool_issuer as issuer
from tests.factories.journal import start_run
from tests.unit.services.test_group_a_verified_ingress_authorities import (
    FIELDS,
    _test_private_key,
    proof,
    roots,
)


def fixture(tmp_path: Path, monkeypatch, *, native_policy: str | None = None):
    import hashlib

    from oh_goon.core.models.fleet_executor import (
        FleetExecutorPlacement,
        PlanExecutorCapacityEvidence,
    )
    from oh_goon.core.models.fleet_plan_evidence import (
        PlanIsolationBackendEvidence,
        PlanSnapshotEvidence,
    )
    from oh_goon.core.models.fleet_plan_execution_policy import (
        CicdExecutionPolicy,
        VerificationExecutionPolicy,
    )
    from oh_goon.core.models.repository import RepositoryIdentity
    from oh_goon.core.models.run_admission import (
        DirectFleetExecutionAdmission,
        FleetRunAdmissionIntent,
        PlanRepositoryAdmissionEvidence,
        StrictFleetExecutionAdmission,
        fleet_repository_scope,
    )
    from oh_goon.core.models.source_authority import RepositorySourceAuthority
    from oh_goon.core.vocabulary import OperationAction
    from oh_goon.runtime.database import LocalDatabase
    from oh_goon.runtime.journal import Journal
    from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
    from tests.factories.capabilities import capability_snapshot
    from tests.factories.fleet_plan_targets import structural_fleet_plan_target
    from tests.factories.journal import admit_run
    from tests.unit.services.test_group_a_verified_ingress_authorities import setup

    plan_file = tmp_path / "goon.yml"
    plan_file.write_text(
        "version: 1\nexecution:\n  purpose: verification\nsteps:\n  - id: check\n    run: 'true'\n"
    )
    raw = FleetPlanResolver().resolve(plan_file)
    from oh_goon.core.models.fleet_plan import ResolvedAgentStep, ResolvedFleetPlan

    native = None
    steps = raw.steps
    if native_policy is not None:
        from oh_goon.core.models.agent_providers import (
            NativeAgentDefinition,
            NativeAgentProfile,
            ResolvedNativeAgent,
        )

        native = ResolvedNativeAgent(
            agent="pi",
            profile_name="selected",
            definition=NativeAgentDefinition(executable="/selected/pi-custom"),
            profile=NativeAgentProfile(),
            executable_sha256="a" * 64,
        )
        steps = (
            ResolvedAgentStep(
                workload={"harness": "native", "native_agent": native},
                step_id="check",
                timeout_seconds=30,
                delivery_sha256="b" * 64,
                handoff_identity="handoff",
                source_step_id="check",
            ),
        )

    plan = ResolvedFleetPlan.create(
        name=raw.name,
        description=raw.description,
        steps=steps,
        execution=(
            VerificationExecutionPolicy() if native_policy == "ordinary" else CicdExecutionPolicy()
        ),
        requirements=raw.requirements,
        imports=raw.imports,
        step_sources=raw.step_sources,
        native_agent=native,
    )
    if native is None:
        target = structural_fleet_plan_target(plan)
    else:
        from oh_goon.core.models.goon_plan_source import PlanFileSource
        from oh_goon.core.models.target import AgentStepDeliveryReference, FleetPlanTarget
        from tests.factories.fleet_plan_targets import structural_execution_identity

        target = FleetPlanTarget(
            kind="fleet_plan",
            plan=plan,
            source=PlanFileSource(sha256="0" * 64),
            identity=structural_execution_identity(plan),
            agent_deliveries=(
                AgentStepDeliveryReference(
                    step_id="check",
                    handoff_identity="handoff",
                    delivery_sha256="b" * 64,
                    reference_id="retained-native-delivery",
                ),
            ),
        )
    identities = []
    admissions = []
    for ch in "ab":
        root = tmp_path / f"repo-{ch}"
        root.mkdir()
        (root / "code.txt").write_text(ch)
        repository = RepositoryIdentity(path=root)
        identities.append(repository)
        snapshot = SnapshotIdentity(hashlib.sha256(ch.encode()).hexdigest(), 1, 1)
        source = RepositorySourceAuthority.create(
            subject_instance_id="repo-subject-instance-" + ch * 64,
            source_snapshot=PlanSnapshotEvidence(
                sha256=snapshot.sha256,
                byte_count=1,
                entry_count=1,
            ),
        )
        admissions.append(
            PlanRepositoryAdmissionEvidence.create(
                repository_id=repository.repository_id,
                plan_id=plan.plan_id,
                semantic_preview_id="fleet-plan-repository-preview-" + ch * 64,
                source_authority=source,
                active_step_ids=("check",),
            )
        )
    identities.sort(key=lambda obj: obj.repository_id)
    admissions.sort(key=lambda obj: obj.repository_id)
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
    intent = FleetRunAdmissionIntent(
        target=target,
        capability=capability_snapshot(OperationAction.EXECUTE_PLAN, target),
        execution=DirectFleetExecutionAdmission()
        if native_policy == "ordinary"
        else StrictFleetExecutionAdmission(
            placement=FleetExecutorPlacement(
                isolation_backend=backend,
                executor_capacity=capacity,
                executor_trust_epoch=0,
            )
        ),
        repository_admissions=tuple(admissions),
    )
    journal = Journal(LocalDatabase(tmp_path / "run.sqlite3"))
    admit_run(
        journal,
        "durable-run",
        intent,
        fleet_repository_scope(set(item.repository_id for item in identities)),
        identities,
    )
    root = tmp_path / "deployment"
    bin_dir = root / "bin"
    bin_dir.mkdir(parents=True)
    executable = bin_dir / "true"
    executable.write_bytes(b"#!/bin/sh\nexit 0\n")
    executable.chmod(0o755)
    inspector = root / "inspector"
    inspector.write_text("inspector")
    inspector.chmod(0o755)
    authority = StrictHostPathAuthority.create(
        trusted_roots=(root,),
        tool_search_paths=(bin_dir,),
        runtime_inspector=inspector,
    )

    # Hashmarks is unavailable in this environment. Substitute only its
    # content observer, not Plan selection, path resolution or journal replay.
    def measured(path):
        payload = path.read_bytes()
        return SnapshotIdentity(hashlib.sha256(payload).hexdigest(), len(payload), 1)

    monkeypatch.setattr(content_snapshot, "snapshot_identity", measured)
    _, _, _, base_contract, _, _ = setup()
    fields = base_contract.fields
    fields["run_id"] = "durable-run"
    if native_policy is None:
        fields["native_tool_identity"] = issuer.observed_admitted_run_native_tool_identity(
            journal=journal,
            run_id="durable-run",
            path_authority=authority,
        )
    return journal, AdmittedRunContract.freeze(fields), authority, executable


def sign(journal, contract, authority):
    return issuer.issue_observed_captured_run_native_tool_identity(
        journal=journal,
        contract=contract,
        path_authority=authority,
        issuer_id=roots()["native_tool_identity"][0],
        signing_key=_test_private_key("native_tool_identity"),
    )


def test_exact_run_selected_native_tool_signed_and_independently_verified(tmp_path, monkeypatch):
    journal, contract, authority, _ = fixture(tmp_path, monkeypatch)
    signed = sign(journal, contract, authority)
    assert signed.field == "native_tool_identity"
    proofs = tuple(
        signed if field == "native_tool_identity" else proof(field, contract) for field in FIELDS
    )
    TrustedIssuerVerifier(roots()).verify(
        contract,
        evidence=proofs,
        control_epoch=1,
        lease_identity="lease",
        lease_is_current=lambda *_: True,
    )
    assert signed == sign(journal, contract, authority)


@pytest.mark.parametrize("native_policy", ("ordinary", "strict"))
def test_native_agent_selection_rejects_before_host_tool_observation_or_signing(
    tmp_path, monkeypatch, native_policy
):
    journal, contract, authority, _ = fixture(tmp_path, monkeypatch, native_policy=native_policy)

    def forbidden_observation(*_args):
        raise AssertionError("unsupported native authority cannot capture host-tool bytes")

    monkeypatch.setattr(issuer, "_observe", forbidden_observation)
    with pytest.raises(
        TrustProtocolError, match="Native Agent executable authority is unsupported"
    ):
        issuer.observed_admitted_run_native_tool_identity(
            journal=journal, run_id="durable-run", path_authority=authority
        )
    with pytest.raises(
        TrustProtocolError, match="Native Agent executable authority is unsupported"
    ):
        sign(journal, contract, authority)


def test_modified_tool_bytes_rejected(tmp_path, monkeypatch):
    journal, contract, authority, executable = fixture(tmp_path, monkeypatch)
    executable.write_bytes(b"#!/bin/sh\nexit 1\n")
    with pytest.raises(TrustProtocolError, match="contract native tool identity differs"):
        sign(journal, contract, authority)


def test_tool_missing_fails_closed(tmp_path, monkeypatch):
    journal, contract, authority, executable = fixture(tmp_path, monkeypatch)
    executable.unlink()
    with pytest.raises(TrustProtocolError, match="observation unavailable"):
        sign(journal, contract, authority)


def test_tool_swapped_between_observations_rejected(tmp_path, monkeypatch):
    journal, contract, authority, executable = fixture(tmp_path, monkeypatch)
    original = content_snapshot.snapshot_identity
    calls = 0

    def changed(path):
        nonlocal calls
        calls += 1
        result = original(path)
        if calls == 1:
            executable.write_bytes(b"replacement")
        return result

    monkeypatch.setattr(content_snapshot, "snapshot_identity", changed)
    with pytest.raises(TrustProtocolError, match="changed during (issuance|owner observation)"):
        sign(journal, contract, authority)


def test_run_started_during_observation_rejected(tmp_path, monkeypatch):
    journal, contract, authority, _ = fixture(tmp_path, monkeypatch)
    original = content_snapshot.snapshot_identity
    calls = 0

    def changed(path):
        nonlocal calls
        calls += 1
        value = original(path)
        if calls == 1:
            start_run(journal, "durable-run")
        return value

    monkeypatch.setattr(content_snapshot, "snapshot_identity", changed)
    with pytest.raises(TrustProtocolError, match="sole committed admission"):
        sign(journal, contract, authority)


def test_wrong_contract_tool_identity_rejected(tmp_path, monkeypatch):
    journal, contract, authority, _ = fixture(tmp_path, monkeypatch)
    fields = contract.fields
    fields["native_tool_identity"] = "sha256:" + "0" * 64
    with pytest.raises(TrustProtocolError, match="contract native tool identity differs"):
        sign(journal, AdmittedRunContract.freeze(fields), authority)


def test_missing_signer_rejected_before_tool_observation(tmp_path, monkeypatch):
    journal, contract, authority, _ = fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(
        content_snapshot,
        "snapshot_identity",
        lambda _: (_ for _ in ()).throw(AssertionError("no observation allowed")),
    )
    with pytest.raises(TrustProtocolError, match="independent native tool signing"):
        issuer.issue_observed_captured_run_native_tool_identity(
            journal=journal,
            contract=contract,
            path_authority=authority,
            issuer_id=roots()["native_tool_identity"][0],
            signing_key=None,
        )


def test_noncanonical_deployment_path_authority_rejected(tmp_path, monkeypatch):
    journal, contract, authority, _ = fixture(tmp_path, monkeypatch)
    unsafe = StrictHostPathAuthority(
        trusted_roots=authority.trusted_roots,
        tool_search_paths=(tmp_path / "elsewhere",),
        runtime_inspector=authority.runtime_inspector,
    )
    with pytest.raises(TrustProtocolError, match="deployment tool authority"):
        sign(journal, contract, unsafe)


def test_no_unsigned_root_fallback(tmp_path, monkeypatch):
    journal, contract, authority, _ = fixture(tmp_path, monkeypatch)
    with pytest.raises(TrustProtocolError, match="deployment-owned"):
        sign(journal, contract, None)


def test_same_bytes_replaced_inode_between_observations_rejected(tmp_path, monkeypatch):
    journal, contract, authority, executable = fixture(tmp_path, monkeypatch)
    original = content_snapshot.snapshot_identity
    calls = 0

    def relink(path):
        nonlocal calls
        calls += 1
        result = original(path)
        if calls == 1:
            replaced = executable.with_suffix(".new")
            replaced.write_bytes(executable.read_bytes())
            replaced.chmod(0o755)
            replaced.replace(executable)
        return result

    monkeypatch.setattr(content_snapshot, "snapshot_identity", relink)
    with pytest.raises(TrustProtocolError, match="changed during (issuance|owner observation)"):
        sign(journal, contract, authority)


def test_tool_loses_execute_permission_fails_closed(tmp_path, monkeypatch):
    journal, contract, authority, executable = fixture(tmp_path, monkeypatch)
    executable.chmod(0o644)
    with pytest.raises(TrustProtocolError, match="observation unavailable"):
        sign(journal, contract, authority)


def test_no_private_key_material_in_verification_only_evidence(tmp_path, monkeypatch):
    journal, contract, authority, _ = fixture(tmp_path, monkeypatch)
    signed = sign(journal, contract, authority)
    assert len(signed.signature_hex) == 128
    assert (
        _test_private_key("native_tool_identity").private_bytes_raw().hex()
        not in signed.payload.hex()
    )


def test_native_hashmarks_absence_fails_closed(tmp_path, monkeypatch):
    journal, contract, authority, _ = fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(
        content_snapshot,
        "snapshot_identity",
        lambda _: (_ for _ in ()).throw(ImportError("Hashmarks unavailable")),
    )
    with pytest.raises(TrustProtocolError, match="observation unavailable"):
        sign(journal, contract, authority)
