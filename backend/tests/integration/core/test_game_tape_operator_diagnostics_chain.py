from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

pytest.importorskip("hashmarks")

from tests.factories.capabilities import capability_snapshot
from tests.factories.fleet_plan_targets import structural_execution_identity
from tests.factories.operation_reports import make_operation_report
from tests.factories.run_admission import repository_admissions_for
from tests.hashmarks_test_support import (
    TEST_HASHMARKS_ARTIFACT_IDENTITY,
    TEST_HASHMARKS_PRODUCER_IMPLEMENTATION_IDENTITY,
)

from oh_goon.core.errors import ErrorKind
from oh_goon.core.models.execution_authority_diagnostics import (
    PlanExecutionAuthorityAtomicHandoffEvidence,
    PlanExecutionAuthorityDiagnosticEvidence,
    PlanExecutionAuthorityMaterializationEvidence,
    PlanExecutionAuthorityRuntimeEvidence,
)
from oh_goon.core.models.explanation import ReasonCode
from oh_goon.core.models.fleet_plan import ResolvedCommandStep, ResolvedFleetPlan
from oh_goon.core.models.fleet_plan_evidence import (
    CompletedPlanStepExecution,
    FleetPlanResult,
    PlanEgressEvidence,
    PlanExecutionAuthorityEconomicsEvidence,
    PlanOutputEvidence,
    PlanStepAttemptEvidence,
    PlanStepSettlementEvidence,
)
from oh_goon.core.models.fleet_plan_execution_policy import MaintenanceExecutionPolicy
from oh_goon.core.models.goon_plan_source import PlanFileSource
from oh_goon.core.models.operation import FleetPlanOperationEvidence
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.run import (
    CompletedFleetRun,
    FailedFleetRun,
    FleetRunDetail,
    FleetRunFailure,
    FleetRunProgress,
)
from oh_goon.core.models.run_admission import DirectFleetExecutionAdmission, fleet_repository_scope
from oh_goon.core.models.shard_execution import (
    CanonicalShardAttemptEvidence,
    CanonicalShardExecutionEvidence,
    CanonicalShardResultEvidence,
)
from oh_goon.core.models.target import FleetPlanTarget
from oh_goon.core.time import utc_now
from oh_goon.core.vocabulary import FleetRunFailureStage, FleetRunStatus, OperationAction, Verdict

pytestmark = pytest.mark.dependency_complete


def _output(value: str) -> PlanOutputEvidence:
    raw = value.encode()
    return PlanOutputEvidence(
        sha256=hashlib.sha256(raw).hexdigest(),
        byte_count=len(raw),
        line_count=len(value.splitlines()),
    )


def _plan() -> tuple[ResolvedFleetPlan, ResolvedCommandStep]:
    step = ResolvedCommandStep(
        step_id="verify", argv=("sh", "scripts/verify.sh"), timeout_seconds=30
    )
    return ResolvedFleetPlan.create(
        name="operator-diagnostics",
        description="",
        execution=MaintenanceExecutionPolicy(),
        steps=(step,),
    ), step


def _target(plan: ResolvedFleetPlan) -> FleetPlanTarget:
    return FleetPlanTarget(
        kind="fleet_plan",
        plan=plan,
        source=PlanFileSource(sha256="1" * 64),
        identity=structural_execution_identity(plan),
    )


def _attempt(step: ResolvedCommandStep) -> PlanStepAttemptEvidence:
    materialization = "sha256:" + "2" * 64
    runtime = "sha256:" + "3" * 64
    authority = PlanExecutionAuthorityDiagnosticEvidence(
        materialization=PlanExecutionAuthorityMaterializationEvidence(
            receipt_identity=materialization,
            logical_sources=("Makefile",),
            owned_scripts=("scripts/verify.sh",),
        ),
        runtime=PlanExecutionAuthorityRuntimeEvidence(
            receipt_identity=runtime,
            executable_path="/toolchain/bin/sh",
            executable_identity="sha256:" + "4" * 64,
            path_authority_identity="sha256:" + "5" * 64,
            environment_identity="sha256:" + "6" * 64,
            execution_materialization_identity=materialization,
        ),
        atomic_handoff=PlanExecutionAuthorityAtomicHandoffEvidence(
            state="PROVEN", mechanism="sealed_memfd_bind", terminal_proof_bytes=128
        ),
    )
    economics = PlanExecutionAuthorityEconomicsEvidence(
        scope="runtime-launch",
        candidate_units=2,
        reused_units=1,
        recomputed_units=1,
        reused_bytes=64,
        recomputed_bytes=64,
        terminal_proof_bytes=128,
        reuse_provenance=("prepared-host-tool",),
        reuse_identities=("sha256:" + "7" * 64,),
        recompute_reasons=("mutable-attempt-materialization",),
        receipt_identity="sha256:" + "8" * 64,
    )
    return PlanStepAttemptEvidence(
        attempt=1,
        argv=step.argv,
        returncode=0,
        stdout_evidence=_output("passed\n"),
        stderr_evidence=_output(""),
        repository_state_before="before",
        repository_state_after="after",
        egress=PlanEgressEvidence(mode="none"),
        execution_authority_economics=(economics,),
        execution_authority=authority,
    )


def _shards(
    runtime_receipt: str,
    *,
    producer_identity: str | None = TEST_HASHMARKS_PRODUCER_IMPLEMENTATION_IDENTITY,
) -> CanonicalShardExecutionEvidence:
    member = "tests/test_verify.py::test_verify"
    return CanonicalShardExecutionEvidence(
        plan_identity="sha256:" + "9" * 64,
        repository_generation_identity="sha256:" + "a" * 64,
        execution_context_identity="sha256:" + "b" * 64,
        execution_policy_identity="sha256:" + "c" * 64,
        producer_contract_schema="hashmarks.verification-selection.v1",
        producer_version="0.14.0",
        producer_artifact_identity=TEST_HASHMARKS_ARTIFACT_IDENTITY,
        producer_implementation_identity=producer_identity,
        producer_contract_identity="sha256:" + "d" * 64,
        repository_content_identity="sha256:" + "e" * 64 + ":2",
        verification_selection_evidence_identity="sha256:" + "f" * 64,
        selected_membership_identity="sha256:" + "1" * 64,
        verification_revision_identity="sha256:" + "2" * 64,
        selected_members=(member,),
        actual_executed_membership_identity="sha256:" + "3" * 64,
        actual_executed_members=(member,),
        results=(
            CanonicalShardResultEvidence(
                shard_id="hashmarks-verification-0",
                described_work_identity="sha256:" + "4" * 64,
                physical_work_identity="sha256:" + "5" * 64,
                status="PASS",
                members=(member,),
                attempts=(
                    CanonicalShardAttemptEvidence(
                        attempt_identity="sha256:" + "6" * 64,
                        attempt=1,
                        physical_work_identity="sha256:" + "5" * 64,
                        status="PASS",
                        returncode=0,
                        duration_ms=12,
                        runtime_authority_receipt_identity=runtime_receipt,
                        supervisor_receipt_identity="sha256:" + "7" * 64,
                        process_group_quiescent=True,
                        forced_termination=False,
                    ),
                ),
            ),
        ),
    )


def _completed_detail(
    tmp_path: Path,
    *,
    producer_identity: str | None = TEST_HASHMARKS_PRODUCER_IMPLEMENTATION_IDENTITY,
) -> FleetRunDetail:
    repository = RepositoryIdentity(path=tmp_path / "repo")
    plan, step = _plan()
    target = _target(plan)
    attempt = _attempt(step)
    assert (
        attempt.execution_authority is not None and attempt.execution_authority.runtime is not None
    )
    execution = CompletedPlanStepExecution(
        step_id=step.step_id,
        kind="command",
        settlement=PlanStepSettlementEvidence(
            outcome="SUCCESS", cleanup="VERIFIED", reason="ACCEPTED_EXIT", observed_returncode=0
        ),
        argv=step.argv,
        stdout_evidence=attempt.stdout_evidence,
        stderr_evidence=attempt.stderr_evidence,
        repository_state_before="before",
        repository_state_after="after",
        attempts=(attempt,),
    )
    result = FleetPlanResult(
        plan_id=plan.plan_id,
        steps=(execution,),
        shard_execution=_shards(
            attempt.execution_authority.runtime.receipt_identity,
            producer_identity=producer_identity,
        ),
    )
    report = make_operation_report(
        repository,
        OperationAction.EXECUTE_PLAN,
        verdict=Verdict.READY,
        reason_code=ReasonCode.READY,
        fleet_run_id="run-operator",
        target=target,
        evidence=FleetPlanOperationEvidence(kind="FLEET_PLAN", plan=result),
    )
    now = utc_now()
    run = CompletedFleetRun(
        status=FleetRunStatus.COMPLETED,
        fleet_run_id="run-operator",
        target=target,
        capability=capability_snapshot(OperationAction.EXECUTE_PLAN, target),
        repository_scope=fleet_repository_scope(None),
        targets=(repository,),
        accepted_at=now,
        started_at=now,
        finished_at=now,
        progress=FleetRunProgress(total=1, succeeded=1, failed=0, needs_attention=0),
    )
    return FleetRunDetail(
        repository_admissions=repository_admissions_for(run.target, run.targets),
        run=run,
        execution=DirectFleetExecutionAdmission(),
        operations=(report,),
    )


def test_successful_operator_projection_survives_durable_json_roundtrip(tmp_path: Path) -> None:
    detail = _completed_detail(tmp_path)
    source_result = detail.operations[0].evidence.plan  # type: ignore[union-attr]
    assert source_result.shard_execution is not None
    source_identity = source_result.shard_execution.evidence_identity

    restored = FleetRunDetail.model_validate_json(
        detail.model_dump_json(exclude_computed_fields=True)
    )
    tape = restored.game_tape
    step = tape.repositories[0].steps[0]
    verification = tape.repositories[0].verification_execution

    assert step.execution_authority is not None and step.execution_authority.runtime is not None
    assert step.execution_authority.runtime.receipt_identity == "sha256:" + "3" * 64
    assert step.execution_authority.atomic_handoff.mechanism == "sealed_memfd_bind"
    assert step.attempts[-1].execution_authority_economics[0].terminal_proof_bytes == 128
    assert (
        tape.repositories[0].shard_lineage[0].attempts[0].runtime_authority_receipt_identity
        == step.execution_authority.runtime.receipt_identity
    )
    assert verification is not None
    assert (
        verification.producer_implementation_identity
        == TEST_HASHMARKS_PRODUCER_IMPLEMENTATION_IDENTITY
    )

    restored_result = restored.operations[0].evidence.plan  # type: ignore[union-attr]
    assert restored_result.shard_execution is not None
    assert restored_result.shard_execution.evidence_identity == source_identity


def test_authority_refusal_why_not_roundtrips_from_durable_failure_only(tmp_path: Path) -> None:
    repository = RepositoryIdentity(path=tmp_path / "repo")
    plan, _ = _plan()
    target = _target(plan)
    failure = FleetRunFailure(
        code="EXECUTION_AUTHORITY_DIVERGENCE",
        kind=ErrorKind.CONFLICT,
        retryable=False,
        stage=FleetRunFailureStage.EXECUTION,
        message="sealed runtime executable bytes diverged",
    )
    now = utc_now()
    run = FailedFleetRun(
        status=FleetRunStatus.FAILED,
        fleet_run_id="run-refusal",
        target=target,
        capability=capability_snapshot(OperationAction.EXECUTE_PLAN, target),
        repository_scope=fleet_repository_scope(None),
        targets=(repository,),
        accepted_at=now,
        finished_at=now,
        failure=failure,
        progress=FleetRunProgress(total=1, succeeded=0, failed=1, needs_attention=1),
    )
    detail = FleetRunDetail(
        repository_admissions=repository_admissions_for(run.target, run.targets),
        run=run,
        execution=DirectFleetExecutionAdmission(),
    )

    restored = FleetRunDetail.model_validate_json(
        detail.model_dump_json(exclude_computed_fields=True)
    )
    diagnostic = restored.game_tape.why_not
    assert diagnostic is not None
    assert diagnostic.code == failure.code
    assert diagnostic.message == failure.message
    assert diagnostic.authority_owner == "execution_authority"
    assert diagnostic.retryable is False
    assert "fresh admission" in diagnostic.safe_next_action
    assert restored.run.failure == failure  # type: ignore[union-attr]
