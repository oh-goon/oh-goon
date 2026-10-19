from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from oh_goon.core.game_tape_failure_history import project_game_tape_failure_history
from oh_goon.core.models.explanation import ReasonCode
from oh_goon.core.models.fleet_plan_evidence import (
    CompletedPlanStepExecution,
    CompletedPlanStepTeardownEvidence,
    FailedPlanStepExecution,
    FleetPlanResult,
    PlanExecutionInputEvidence,
    PlanIsolationBackendEvidence,
    PlanSnapshotEvidence,
)
from oh_goon.core.models.operation import FleetPlanOperationEvidence
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.run import CompletedFleetRun, FleetRunDetail, FleetRunProgress
from oh_goon.core.models.run_admission import DirectFleetExecutionAdmission, fleet_repository_scope
from oh_goon.core.time import utc_now
from oh_goon.core.vocabulary import FleetRunStatus, OperationAction, Verdict
from tests.factories.capabilities import capability_snapshot
from tests.factories.operation_reports import make_operation_report
from tests.factories.run_admission import repository_admissions_for
from tests.unit.core.test_game_tape_projection import _output, _plan, _target


def _failed_detail(
    repository: RepositoryIdentity,
    *,
    run_id: str,
    stderr: str,
    command: tuple[str, ...] = ("pytest",),
    duration_ms: int = 3000,
    workspace_sha256: str = "a" * 64,
) -> FleetRunDetail:
    plan = _plan()
    target = _target(plan)
    now = utc_now()
    step = FailedPlanStepExecution(
        step_id="test",
        kind="command",
        started_at=now,
        finished_at=now + timedelta(milliseconds=duration_ms),
        argv=command,
        returncode=1,
        stdout_evidence=_output("failed\n"),
        stderr_evidence=_output(stderr),
        duration_ms=duration_ms,
        repository_state_before="before",
        repository_state_after="before",
        teardown=CompletedPlanStepTeardownEvidence(
            attempt_roots_observed=0, step_attempts_root_absent=True
        ),
    )
    execution_inputs = PlanExecutionInputEvidence(
        source_authority_id="repository-source-authority-" + "0" * 64,
        repository_id=repository.repository_id,
        base_workspace=PlanSnapshotEvidence(sha256=workspace_sha256, byte_count=1, entry_count=1),
        runtime_root=PlanSnapshotEvidence(sha256="f" * 64, byte_count=1, entry_count=1),
        isolation_backend=PlanIsolationBackendEvidence(
            backend_id="test-isolation",
            configuration_id="isolation-config",
            kernel_boundary="shared_host_kernel",
            remote_execution=False,
            experimental=False,
        ),
    )
    result = FleetPlanResult(plan_id=plan.plan_id, steps=(step,), execution_inputs=execution_inputs)
    report = make_operation_report(
        repository,
        OperationAction.EXECUTE_PLAN,
        Verdict.FAILED,
        ReasonCode.TESTS_FAILED,
        fleet_run_id=run_id,
        target=target,
        evidence=FleetPlanOperationEvidence(kind="FLEET_PLAN", plan=result),
    )
    report = report.model_copy(
        update={
            "provenance": report.provenance.model_copy(
                update={"started_at": now, "finished_at": now + timedelta(milliseconds=duration_ms)}
            )
        }
    )
    run = CompletedFleetRun(
        status=FleetRunStatus.COMPLETED,
        fleet_run_id=run_id,
        target=target,
        capability=capability_snapshot(OperationAction.EXECUTE_PLAN, target),
        repository_scope=fleet_repository_scope(None),
        targets=(repository,),
        accepted_at=now,
        started_at=now,
        finished_at=now + timedelta(milliseconds=duration_ms),
        progress=FleetRunProgress(total=1, succeeded=0, failed=1, needs_attention=1),
    )
    return FleetRunDetail(
        repository_admissions=repository_admissions_for(run.target, run.targets),
        run=run,
        execution=DirectFleetExecutionAdmission(),
        operations=(report,),
    )


def _passed_detail(
    repository: RepositoryIdentity,
    *,
    run_id: str,
    duration_ms: int = 1800,
    workspace_sha256: str = "a" * 64,
    command: tuple[str, ...] = ("pytest",),
) -> FleetRunDetail:
    plan = _plan()
    target = _target(plan)
    now = utc_now()
    step = CompletedPlanStepExecution(
        step_id="test",
        kind="command",
        started_at=now,
        finished_at=now + timedelta(milliseconds=duration_ms),
        argv=command,
        returncode=0,
        stdout_evidence=_output("passed\n"),
        stderr_evidence=_output(""),
        duration_ms=duration_ms,
        repository_state_before="before",
        repository_state_after="after",
        teardown=CompletedPlanStepTeardownEvidence(
            attempt_roots_observed=0, step_attempts_root_absent=True
        ),
    )
    execution_inputs = PlanExecutionInputEvidence(
        source_authority_id="repository-source-authority-" + "0" * 64,
        repository_id=repository.repository_id,
        base_workspace=PlanSnapshotEvidence(sha256=workspace_sha256, byte_count=1, entry_count=1),
        runtime_root=PlanSnapshotEvidence(sha256="f" * 64, byte_count=1, entry_count=1),
        isolation_backend=PlanIsolationBackendEvidence(
            backend_id="test-isolation",
            configuration_id="isolation-config",
            kernel_boundary="shared_host_kernel",
            remote_execution=False,
            experimental=False,
        ),
    )
    result = FleetPlanResult(plan_id=plan.plan_id, steps=(step,), execution_inputs=execution_inputs)
    report = make_operation_report(
        repository,
        OperationAction.EXECUTE_PLAN,
        Verdict.READY,
        ReasonCode.READY,
        fleet_run_id=run_id,
        target=target,
        evidence=FleetPlanOperationEvidence(kind="FLEET_PLAN", plan=result),
    )
    report = report.model_copy(
        update={
            "provenance": report.provenance.model_copy(
                update={"started_at": now, "finished_at": now + timedelta(milliseconds=duration_ms)}
            )
        }
    )
    run = CompletedFleetRun(
        status=FleetRunStatus.COMPLETED,
        fleet_run_id=run_id,
        target=target,
        capability=capability_snapshot(OperationAction.EXECUTE_PLAN, target),
        repository_scope=fleet_repository_scope(None),
        targets=(repository,),
        accepted_at=now,
        started_at=now,
        finished_at=now + timedelta(milliseconds=duration_ms),
        progress=FleetRunProgress(total=1, succeeded=1, failed=0, needs_attention=0),
    )
    return FleetRunDetail(
        repository_admissions=repository_admissions_for(run.target, run.targets),
        run=run,
        execution=DirectFleetExecutionAdmission(),
        operations=(report,),
    )


def test_failure_history_groups_only_exact_prior_signatures(tmp_path: Path) -> None:
    repository = RepositoryIdentity(path=tmp_path / "repo")
    current = _failed_detail(repository, run_id="run-current", stderr="same trace\n")
    exact = _failed_detail(
        repository,
        run_id="run-exact",
        stderr="same trace\n",
        duration_ms=4200,
        workspace_sha256="b" * 64,
    )
    different = _failed_detail(repository, run_id="run-different", stderr="different trace\n")
    current_tape = current.game_tape
    repository_id = current_tape.repositories[0].repository.repository_id

    history = project_game_tape_failure_history(
        current.game_tape,
        (exact.game_tape, different.game_tape),
        repository_id=repository_id,
        step_id="test",
        sample_limit=10,
    )

    assert history.exact_match_rule == "SAME_PLAN_REPOSITORY_STEP_SIGNATURE"
    assert history.history_candidates_considered == 2
    assert history.same_plan_step_failures == 2
    assert [item.fleet_run_id for item in history.occurrences] == ["run-exact"]
    assert history.occurrences[0].signature_id == history.signature_id
    assert history.occurrences[0].duration_ms == 4200
    assert history.occurrences[0].context.command == "MATCH"
    assert history.occurrences[0].context.workspace == "DIFFERENT"
    assert history.occurrences[0].context.runtime == "MATCH"
    assert history.occurrences[0].context.toolchain == "MATCH"
    assert history.occurrences[0].context.isolation == "MATCH"


def test_failure_history_reports_later_pass_and_exact_recurrence(tmp_path: Path) -> None:
    repository = RepositoryIdentity(path=tmp_path / "repo")
    prior_exact = _failed_detail(repository, run_id="run-prior", stderr="same trace\n")
    current = _failed_detail(repository, run_id="run-current", stderr="same trace\n")
    later_pass = _passed_detail(repository, run_id="run-pass")
    later_exact = _failed_detail(repository, run_id="run-recurred", stderr="same trace\n")
    tape = current.game_tape
    repository_id = tape.repositories[0].repository.repository_id

    history = project_game_tape_failure_history(
        current.game_tape,
        (prior_exact.game_tape,),
        (later_pass.game_tape, later_exact.game_tape),
        repository_id=repository_id,
        step_id="test",
        sample_limit=10,
    )

    evolution = history.evolution
    assert evolution.exact_occurrence_count == 3
    assert evolution.prior_exact_occurrences == 1
    assert evolution.later_exact_occurrences == 1
    assert evolution.first_seen_run_id == "run-prior"
    assert evolution.last_seen_run_id == "run-recurred"
    assert evolution.later_comparable_runs == 2
    assert evolution.first_later_pass is not None
    assert evolution.first_later_pass.fleet_run_id == "run-pass"
    assert evolution.first_later_pass.context.workspace == "MATCH"
    assert evolution.first_later_pass.context.summary.classification == "SAME_CONTEXT"
    assert evolution.exact_failure_recurred_after_pass is True
    assert evolution.prior_exact_contexts.same_context == 1
    assert evolution.prior_exact_contexts.changed_context == 0
    assert evolution.later_exact_contexts.same_context == 1
    assert evolution.later_pass_contexts.same_context == 1
