from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from oh_goon.core.errors import ErrorKind
from oh_goon.core.game_tape_projection import _why_not
from oh_goon.core.game_tape_step_projection import project_steps
from oh_goon.core.models.fleet_plan import ResolvedCommandStep, ResolvedFleetPlan
from oh_goon.core.models.fleet_plan_evidence import (
    FailedPlanStepExecution,
    PlanEgressEvidence,
    PlanOutputEvidence,
    PlanStepAttemptEvidence,
)
from oh_goon.core.models.fleet_plan_execution_policy import MaintenanceExecutionPolicy
from oh_goon.core.models.execution_authority_diagnostics import (
    PlanExecutionAuthorityAtomicHandoffEvidence,
    PlanExecutionAuthorityDiagnosticEvidence,
    PlanExecutionAuthorityMaterializationEvidence,
    PlanExecutionAuthorityRuntimeEvidence,
)
from oh_goon.core.models.run import FailedFleetRun, FleetRunDetail, FleetRunFailure, FleetRunProgress
from oh_goon.core.vocabulary import FleetRunFailureStage, FleetRunStatus
from oh_goon.integrations._fleet_plan_step_outcome import StepOutcome
from oh_goon.integrations.fleet_plan_attempt_evidence import _execution_authority_diagnostic
from oh_goon.integrations.fleet_plan_process_isolation import IsolatedProcessRequest
from oh_goon.integrations.fleet_plan_runtime_authority import expected_runtime_authority_sources
from tests.factories.cicd_repository import seed_cicd_repository
from tests.factories.fleet_plan_executor import prepare_physical_test_run, strict_test_plan
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.goon_plan_source import PlanFileSource
from oh_goon.core.models.operation import OperationAction
from oh_goon.core.models.run_admission import DirectFleetExecutionAdmission, fleet_repository_scope
from oh_goon.core.models.target import FleetPlanTarget
from oh_goon.core.time import utc_now
from tests.factories.capabilities import capability_snapshot
from tests.factories.fleet_plan_targets import structural_execution_identity
from tests.factories.run_admission import repository_admissions_for


def test_game_tape_authority_projection_reuses_existing_receipts_without_new_authority(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repo"
    seed_cicd_repository(repository)
    step = ResolvedCommandStep(timeout_seconds=30, step_id="check", argv=("true",))
    plan = strict_test_plan(step)

    with prepare_physical_test_run(RepositoryIdentity(path=repository), plan) as run:
        attempt = run.create_attempt(step, 1)
        request = IsolatedProcessRequest(
            attempt=attempt,
            argv=("true",),
            cwd=attempt.paths.workspace,
            env={"PATH": "/toolchain/bin"},
        )
        expected = expected_runtime_authority_sources(request)
        runtime_economics = replace(
            expected.economics,
            terminal_proof_bytes=expected.economics.reused_bytes
            + expected.economics.recomputed_bytes,
        )
        diagnostic = _execution_authority_diagnostic(
            attempt,
            StepOutcome(
                argv=("true",),
                returncode=0,
                runtime_authority=expected.receipt,
                execution_authority_economics=(runtime_economics,),
            ),
        )

    assert diagnostic.materialization.receipt_identity == (
        expected.receipt.execution_materialization_identity
    )
    assert diagnostic.runtime is not None
    assert diagnostic.runtime.receipt_identity == expected.receipt.receipt_identity
    assert diagnostic.runtime.executable_identity == expected.receipt.executable_identity
    assert diagnostic.atomic_handoff.state == "PROVEN"
    assert diagnostic.atomic_handoff.mechanism == "sealed_memfd_bind"
    assert diagnostic.atomic_handoff.terminal_proof_bytes > 0


def test_game_tape_why_not_projects_execution_authority_divergence() -> None:
    failure = FleetRunFailure(
        code="EXECUTION_AUTHORITY_DIVERGENCE",
        kind=ErrorKind.CONFLICT,
        retryable=False,
        stage=FleetRunFailureStage.EXECUTION,
        message="runtime executable bytes diverged",
    )

    diagnostic = _why_not(SimpleNamespace(run=SimpleNamespace(failure=failure)))

    assert diagnostic is not None
    assert diagnostic.code == "EXECUTION_AUTHORITY_DIVERGENCE"
    assert diagnostic.authority_owner == "execution_authority"
    assert diagnostic.retryable is False
    assert "fresh admission" in diagnostic.safe_next_action
    assert "diverged materialization" in diagnostic.safe_next_action



def _output() -> PlanOutputEvidence:
    return PlanOutputEvidence(sha256="0" * 64, byte_count=0, line_count=0)


def _projected_authority() -> PlanExecutionAuthorityDiagnosticEvidence:
    identity = "sha256:" + "1" * 64
    return PlanExecutionAuthorityDiagnosticEvidence(
        materialization=PlanExecutionAuthorityMaterializationEvidence(
            receipt_identity=identity,
            logical_sources=("Makefile",),
            package_executables=("vitest",),
            owned_scripts=("scripts/check.sh",),
        ),
        runtime=PlanExecutionAuthorityRuntimeEvidence(
            receipt_identity="sha256:" + "2" * 64,
            executable_path="/toolchain/bin/sh",
            executable_identity="sha256:" + "3" * 64,
            path_authority_identity="sha256:" + "4" * 64,
            environment_identity="sha256:" + "5" * 64,
            execution_materialization_identity=identity,
        ),
        atomic_handoff=PlanExecutionAuthorityAtomicHandoffEvidence(
            state="PROVEN",
            mechanism="sealed_memfd_bind",
            terminal_proof_bytes=128,
        ),
    )


def test_game_tape_step_reads_authority_from_durable_attempt_evidence() -> None:
    step = ResolvedCommandStep(timeout_seconds=30, step_id="check", argv=("sh", "check.sh"))
    authority = _projected_authority()
    attempt = PlanStepAttemptEvidence(
        attempt=1,
        argv=step.argv,
        returncode=1,
        stdout_evidence=_output(),
        stderr_evidence=_output(),
        repository_state_before="before",
        repository_state_after="after",
        egress=PlanEgressEvidence(mode="none"),
        execution_authority=authority,
    )
    execution = FailedPlanStepExecution(
        step_id=step.step_id,
        kind="command",
        argv=step.argv,
        returncode=1,
        stdout_evidence=_output(),
        stderr_evidence=_output(),
        repository_state_before="before",
        repository_state_after="after",
        attempts=(attempt,),
    )

    projected = project_steps(
        "run-1",
        "repository-1",
        (step,),
        (execution,),
        step_sources={},
        source_label="goon.yml",
        terminal=True,
        operation_reason_code="EXECUTION_AUTHORITY_DIVERGENCE",
    )

    assert projected[0].execution_authority == authority
    assert projected[0].execution_authority is attempt.execution_authority
    assert projected[0].execution_authority.runtime is not None
    assert projected[0].execution_authority.runtime.receipt_identity == "sha256:" + "2" * 64


def test_game_tape_run_projects_durable_authority_failure_as_why_not(tmp_path: Path) -> None:
    repository = RepositoryIdentity(path=tmp_path / "repo")
    step = ResolvedCommandStep(timeout_seconds=30, step_id="check", argv=("true",))
    plan = ResolvedFleetPlan.create(
        name="why-not-test",
        description="",
        execution=MaintenanceExecutionPolicy(),
        steps=(step,),
    )
    target = FleetPlanTarget(
        kind="fleet_plan",
        plan=plan,
        source=PlanFileSource(sha256="0" * 64),
        identity=structural_execution_identity(plan),
    )
    failure = FleetRunFailure(
        code="EXECUTION_AUTHORITY_DIVERGENCE",
        kind=ErrorKind.CONFLICT,
        retryable=False,
        stage=FleetRunFailureStage.EXECUTION,
        message="runtime executable bytes diverged",
    )
    now = utc_now()
    run = FailedFleetRun(
        status=FleetRunStatus.FAILED,
        fleet_run_id="run-diverged",
        target=target,
        capability=capability_snapshot(OperationAction.EXECUTE_PLAN, target),
        repository_scope=fleet_repository_scope(None),
        targets=(repository,),
        accepted_at=now,
        finished_at=now,
        failure=failure,
        progress=FleetRunProgress(total=1, succeeded=0, failed=1, needs_attention=1),
    )

    tape = FleetRunDetail(repository_admissions=repository_admissions_for(run.target, run.targets), run=run, execution=DirectFleetExecutionAdmission()).game_tape

    assert tape.why_not is not None
    assert tape.why_not.code == failure.code
    assert tape.why_not.message == failure.message
    assert tape.why_not.authority_owner == "execution_authority"
    assert "fresh admission" in tape.why_not.safe_next_action
