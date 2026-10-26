from __future__ import annotations

import hashlib
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from oh_goon.core.game_tape_comparison import compare_game_tapes
from oh_goon.core.game_tape_run_facts import (
    project_issue_focus,
    project_repository_failure_focus,
)
from oh_goon.core.models.explanation import ReasonCode
from oh_goon.core.models.fleet_plan import (
    ResolvedCommandStep,
    ResolvedFleetPlan,
    ResolvedStepSource,
)
from oh_goon.core.models.fleet_plan_evidence import (
    CompletedPlanStepExecution,
    FailedPlanStepExecution,
    FleetPlanResult,
    PlanEgressEvidence,
    PlanIsolationBackendEvidence,
    PlanOutputEvidence,
    PlanResourceEvidence,
    PlanStandardIsolationEvidence,
    PlanStepAttemptEvidence,
    PlanStepSettlementEvidence,
    PlanToolEvidence,
)
from oh_goon.core.models.fleet_plan_execution_policy import MaintenanceExecutionPolicy
from oh_goon.core.models.fleet_plan_retry import (
    AttemptsExhaustedPlanRetryDecisionEvidence,
    ScheduledPlanRetryDecisionEvidence,
)
from oh_goon.core.models.fleet_plan_values import (
    HostEnvironmentPart,
    LiteralEnvironmentPart,
    ResolvedEnvironmentBinding,
)
from oh_goon.core.models.goon_plan_source import PlanFileSource
from oh_goon.core.models.operation import FleetPlanOperationEvidence
from oh_goon.core.models.provenance import RepositoryStateFingerprint
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.run import (
    CancelledFleetRun,
    CompletedFleetRun,
    FailedFleetRun,
    FleetRunDetail,
    FleetRunFailure,
    FleetRunProgress,
    FleetRunState,
    RunningFleetRun,
)
from oh_goon.core.models.run_admission import DirectFleetExecutionAdmission, fleet_repository_scope
from oh_goon.core.models.run_events import (
    DescendantsQuiescentEvent,
    ProcessStartedEvent,
    ProcessStateUnknownEvent,
    RunCancelledEvent,
    RunFailedEvent,
    StepCancelledEvent,
)
from oh_goon.core.models.target import FleetPlanTarget
from oh_goon.core.product_identity import repository_attempt_operation_id
from oh_goon.core.time import utc_now
from oh_goon.core.vocabulary import (
    FleetRunEventType,
    FleetRunOutcome,
    FleetRunStatus,
    GameTapeEventType,
    GameTapeStatus,
    GitBranchState,
    OperationAction,
    Verdict,
    WorktreeState,
)
from tests.factories.canonical_journal import CanonicalFleetJournalBuilder
from tests.factories.capabilities import capability_snapshot
from tests.factories.fleet_plan_targets import structural_execution_identity
from tests.factories.operation_reports import make_operation_report
from tests.factories.run_admission import repository_admissions_for


def _output(text: str) -> PlanOutputEvidence:
    encoded = text.encode()
    return PlanOutputEvidence(
        sha256=hashlib.sha256(encoded).hexdigest(),
        byte_count=len(encoded),
        line_count=len(text.splitlines()),
    )


def _failed_attempt() -> PlanStepAttemptEvidence:
    return PlanStepAttemptEvidence(
        attempt=1,
        argv=("pytest",),
        returncode=1,
        stdout_evidence=_output("failed\n"),
        stderr_evidence=_output("attempt trace\n"),
        duration_ms=3000,
        repository_state_before="prepared",
        repository_state_after="prepared",
        tool=PlanToolEvidence(
            name="pytest",
            origin="host",
            sha256="1" * 64,
            byte_count=42,
        ),
        egress=PlanEgressEvidence(mode="none"),
        resources=PlanResourceEvidence(
            cpu_millis=1000,
            memory_bytes=128 * 1024 * 1024,
            max_processes=64,
            cpu_usage_usec=10_000,
            memory_peak_bytes=32 * 1024 * 1024,
            pids_peak=4,
            oom_kill_count=0,
        ),
        retry=AttemptsExhaustedPlanRetryDecisionEvidence(
            exit_code=1,
            policy_reason="configured attempts exhausted",
        ),
    )


def _plan() -> ResolvedFleetPlan:
    environment = (
        ResolvedEnvironmentBinding(
            name="REGISTRY_TOKEN",
            parts=(HostEnvironmentPart(name="REGISTRY_TOKEN"),),
        ),
        ResolvedEnvironmentBinding(
            name="LABEL",
            parts=(LiteralEnvironmentPart(value="secret-literal"),),
        ),
    )
    return ResolvedFleetPlan.create(
        name="game-tape-test",
        description="",
        execution=MaintenanceExecutionPolicy(),
        steps=(
            ResolvedCommandStep(
                step_id="prepare",
                argv=("echo", "prepare"),
                timeout_seconds=30,
                environment=environment,
            ),
            ResolvedCommandStep(step_id="test", argv=("pytest",), timeout_seconds=30),
            ResolvedCommandStep(step_id="publish", argv=("echo", "publish"), timeout_seconds=30),
        ),
        step_sources=(
            ResolvedStepSource(step_id="prepare", source="goon.yml", line=10, column=5),
            ResolvedStepSource(step_id="test", source="goon.yml", line=14, column=5),
            ResolvedStepSource(step_id="publish", source="goon.yml", line=18, column=5),
        ),
    )


def _target(plan: ResolvedFleetPlan) -> FleetPlanTarget:
    return FleetPlanTarget(
        kind="fleet_plan",
        plan=plan,
        source=PlanFileSource(sha256="0" * 64),
        identity=structural_execution_identity(plan),
    )


def _assert_tape_derived_views_are_not_source_facts(tape) -> None:
    source_facts = tape.model_dump(mode="json", exclude_computed_fields=True)
    normal = tape.model_dump(mode="json")
    for field in ("summary", "team", "observability"):
        assert field not in source_facts
        assert field in normal


@pytest.mark.parametrize("duration", [None, 0, 18])
def test_cancelled_step_preserves_unknown_and_observed_durations(duration):
    from oh_goon.core.game_tape_step_projection import project_steps
    from oh_goon.core.models.fleet_plan_evidence import (
        PlanExecutionSupervisorEvidence,
        PlanStepCancellationEvidence,
    )

    step = ResolvedCommandStep(step_id="cancelled", argv=("work",))
    receipt = PlanStepCancellationEvidence(
        settlement=PlanStepSettlementEvidence(
            outcome="INCOMPLETE" if duration is None else "CANCELLED",
            reason="SUPERVISION_UNAVAILABLE" if duration is None else "CALLER_CANCELLED",
            cleanup="UNAVAILABLE" if duration is None else "VERIFIED",
        ),
        duration_ms=duration,
        physical_execution_id=None if duration is None else "original-physical",
        supervisor=None
        if duration is None
        else PlanExecutionSupervisorEvidence(
            process_group_cleanup_requested=True,
            cleanup_reason="cancel",
            final_process_group_quiescent=True,
        ),
    )
    event = StepCancelledEvent(
        event_type=FleetRunEventType.STEP_CANCELLED,
        sequence=1,
        fleet_run_id="cancelled-run",
        repository_id="cancelled-repository",
        operation_id="cancelled-operation",
        step_id=step.step_id,
        step_order=1,
        cancellation=receipt,
    )
    projected = project_steps(
        event.fleet_run_id,
        event.repository_id,
        (step,),
        (),
        step_sources={},
        source_label="plan.yml",
        terminal=True,
        operation_reason_code=None,
        lifecycle_events=(event,),
    )[0]
    assert projected.duration_ms == duration
    assert projected.cancellation == receipt
    assert not projected.attempts and projected.finished_at is None
    assert projected.native_agent_observations == ()


_FAILED_PLAN_PLAY_BY_PLAY = [
    GameTapeEventType.RUN_ADMITTED,
    GameTapeEventType.RUN_STARTED,
    GameTapeEventType.REPOSITORY_STARTED,
    GameTapeEventType.STEP_REACHED,
    GameTapeEventType.STEP_STARTED,
    GameTapeEventType.STEP_PASSED,
    GameTapeEventType.STEP_REACHED,
    GameTapeEventType.STEP_STARTED,
    GameTapeEventType.STEP_FAILED,
    GameTapeEventType.STEP_NOT_REACHED,
    GameTapeEventType.REPOSITORY_FAILED,
    GameTapeEventType.RUN_ATTENTION_REQUIRED,
]


def test_game_tape_projects_plan_order_timing_outcomes_and_safe_provenance(tmp_path: Path) -> None:
    repository = RepositoryIdentity(path=tmp_path / "repo")
    plan = _plan()
    target = _target(plan)
    now = utc_now()
    prepare = CompletedPlanStepExecution(
        step_id="prepare",
        kind="command",
        started_at=now + timedelta(seconds=1),
        finished_at=now + timedelta(seconds=2),
        argv=("echo", "prepare"),
        returncode=0,
        stdout_evidence=_output("ready\n"),
        stderr_evidence=_output(""),
        duration_ms=1000,
        repository_state_before="before",
        repository_state_after="prepared",
    )
    test = FailedPlanStepExecution(
        step_id="test",
        kind="command",
        started_at=now + timedelta(seconds=2),
        finished_at=now + timedelta(seconds=5),
        argv=("pytest",),
        returncode=1,
        stdout_evidence=_output("failed\n"),
        stderr_evidence=_output("trace\n"),
        duration_ms=3000,
        repository_state_before="prepared",
        repository_state_after="prepared",
        attempts=(_failed_attempt(),),
    )
    result = FleetPlanResult(plan_id=plan.plan_id, steps=(prepare, test))
    report = make_operation_report(
        repository,
        OperationAction.EXECUTE_PLAN,
        Verdict.FAILED,
        ReasonCode.TESTS_FAILED,
        fleet_run_id="run-game-tape",
        target=target,
        evidence=FleetPlanOperationEvidence(kind="FLEET_PLAN", plan=result),
    )
    report = report.model_copy(
        update={
            "provenance": report.provenance.model_copy(
                update={
                    "started_at": now,
                    "finished_at": now + timedelta(seconds=5),
                    "before": RepositoryStateFingerprint(
                        captured_at=now,
                        head_sha="73fd91a",
                        branch_state=GitBranchState.NAMED,
                        branch="main",
                        worktree_state=WorktreeState.CLEAN,
                    ),
                }
            )
        }
    )
    run = CompletedFleetRun(
        status=FleetRunStatus.COMPLETED,
        fleet_run_id="run-game-tape",
        target=target,
        capability=capability_snapshot(OperationAction.EXECUTE_PLAN, target),
        repository_scope=fleet_repository_scope(None),
        targets=(repository,),
        accepted_at=now,
        started_at=now,
        finished_at=now + timedelta(seconds=5),
        progress=FleetRunProgress(total=1, succeeded=0, failed=1, needs_attention=1),
    )
    detail = FleetRunDetail(
        repository_admissions=repository_admissions_for(run.target, run.targets),
        run=run,
        execution=DirectFleetExecutionAdmission(),
        operations=(report,),
    )
    operation_id = report.provenance.operation_id
    detail = detail.model_copy(
        update={
            "lifecycle_events": (
                CanonicalFleetJournalBuilder(detail)
                .run_started(now)
                .repository_started(repository.repository_id, operation_id, now)
                .step_reached(
                    repository.repository_id, operation_id, "prepare", 1, prepare.started_at
                )
                .step_started(
                    repository.repository_id, operation_id, "prepare", 1, prepare.started_at
                )
                .step_succeeded(
                    repository.repository_id, operation_id, "prepare", 1, 0, prepare.finished_at
                )
                .step_reached(repository.repository_id, operation_id, "test", 2, test.started_at)
                .step_started(repository.repository_id, operation_id, "test", 2, test.started_at)
                .step_failed(repository.repository_id, operation_id, "test", 2, 1, test.finished_at)
                .repository_completed(operation_id, report.provenance.finished_at)
                .run_completed(run.finished_at)
                .build()
            )
        }
    )
    tape = detail.game_tape
    steps = tape.repositories[0].steps

    assert tape.plan_id == plan.plan_id
    assert tape.execution_digest == target.execution_digest
    assert [step.step_id for step in steps] == ["prepare", "test", "publish"]
    assert [step.order for step in steps] == [1, 2, 3]
    assert [step.status for step in steps] == [
        GameTapeStatus.PASSED,
        GameTapeStatus.FAILED,
        GameTapeStatus.NOT_RUN,
    ]
    assert steps[0].started_at == prepare.started_at
    assert steps[0].finished_at == prepare.finished_at
    assert steps[0].step_execution_id != steps[1].step_execution_id
    assert steps[0].environment[0].sources == ("host:REGISTRY_TOKEN",)
    assert steps[0].environment[1].sources == ("literal",)
    assert "secret-literal" not in tape.model_dump_json()
    assert tape.summary.planned_steps == 3
    assert tape.summary.executed_steps == 2
    assert tape.summary.failed_steps == 1
    assert tape.summary.not_run_steps == 1
    _assert_tape_derived_views_are_not_source_facts(tape)
    assert tape.execution_reference is not None
    assert tape.execution_reference.source == PlanFileSource(sha256="0" * 64)
    assert steps[1].why.previous_step_id == "prepare"
    assert steps[1].why.reached is True
    assert steps[1].why.declaration == "goon.yml:14"
    assert steps[1].why.source_location is not None
    assert steps[1].why.source_location.line == 14
    assert steps[1].why.source_location.column == 5
    assert steps[2].why.reached is False
    assert steps[2].why.previous_step_status is GameTapeStatus.FAILED
    assert steps[1].failure is not None
    assert steps[1].failure.exit_code == 1
    assert steps[1].failure.attempt == 1
    assert steps[1].failure.signature_id.startswith("game-tape-failure-")
    assert steps[1].failure.evidence_stream == "stderr"
    assert steps[1].failure.evidence_line_count == 1
    assert steps[1].failure.evidence_byte_count == len(b"attempt trace\n")
    assert steps[1].failure.raw_log_available is False
    assert steps[1].attempt_count == 1
    assert steps[1].attempts[0].tool is not None
    assert steps[1].attempts[0].tool.sha256 == "1" * 64
    assert steps[1].attempts[0].egress.mode == "none"
    assert steps[1].attempts[0].resources is not None
    assert steps[1].attempts[0].resources.memory_peak_bytes == 32 * 1024 * 1024
    assert steps[1].attempts[0].retry is not None
    assert steps[1].attempts[0].retry.disposition == "ATTEMPTS_EXHAUSTED"
    assert tape.repositories[0].fingerprint.repository_head_sha == "73fd91a"
    assert tape.repositories[0].fingerprint.branch == "main"
    assert tape.repositories[0].bottleneck_step_id == "test"
    assert tape.repositories[0].bottleneck_percent == 75
    assert tape.team.repository_count == 1
    assert tape.team.observed_peak_concurrency == 1
    assert tape.team.overlap_observed is False
    assert tape.issue_count == 1
    assert tape.hidden_issue_count == 0
    assert len(tape.issue_focus) == 1
    assert tape.issue_focus[0].scope == "STEP"
    assert tape.issue_focus[0].repository_name == "repo"
    assert tape.issue_focus[0].step_id == "test"
    assert tape.issue_focus[0].step_execution_id == steps[1].step_execution_id
    assert tape.issue_focus[0].exit_code == 1
    assert tape.issue_focus[0].signature_id == steps[1].failure.signature_id
    with pytest.raises(ValueError, match="complete five-item visible focus"):
        tape.model_copy(update={"issue_count": 2}).validate_issue_focus_counts()
    repeated_failures = project_repository_failure_focus(
        tuple(tape.repositories[0] for _ in range(6))
    )
    issue_count, issue_focus = project_issue_focus(
        detail,
        repeated_failures,
    )
    assert issue_count == 6
    assert len(issue_focus) == 5
    assert issue_count - len(issue_focus) == 1
    first_repository_failure = tape.repositories[0].model_copy(
        update={
            "repository_execution_id": "repository-first",
            "repository": RepositoryIdentity(path=tmp_path / "repository-first"),
            "steps": (),
        }
    )
    later_step_failure = tape.repositories[0].model_copy(
        update={
            "repository_execution_id": "repository-second",
            "repository": RepositoryIdentity(path=tmp_path / "repository-second"),
        }
    )
    mixed_failures = project_repository_failure_focus(
        (first_repository_failure, later_step_failure)
    )
    mixed_count, mixed_focus = project_issue_focus(
        detail,
        mixed_failures,
    )
    assert mixed_count == 2
    assert [issue.scope for issue in mixed_focus] == ["REPOSITORY", "STEP"]
    assert [issue.repository_execution_id for issue in mixed_focus] == [
        "repository-first",
        "repository-second",
    ]
    assert mixed_count - len(mixed_focus) == 0
    assert tape.observability.terminal_handoff_seconds == 600
    assert tape.observability.persistent_raw_output_store is False
    assert [event.event_type for event in tape.play_by_play] == _FAILED_PLAN_PLAY_BY_PLAY
    assert [event.sequence for event in tape.play_by_play] == list(range(1, 13))
    assert tape.play_by_play[3].step_execution_id == steps[0].step_execution_id
    assert tape.play_by_play[6].step_execution_id == steps[1].step_execution_id


def test_recovery_failure_is_interrupted_without_inventing_step_execution(tmp_path: Path) -> None:
    repository = RepositoryIdentity(path=tmp_path / "repo")
    plan = _plan()
    target = _target(plan)
    now = utc_now()
    run = FailedFleetRun(
        status=FleetRunStatus.FAILED,
        fleet_run_id="run-interrupted",
        target=target,
        capability=capability_snapshot(OperationAction.EXECUTE_PLAN, target),
        repository_scope=fleet_repository_scope(None),
        targets=(repository,),
        accepted_at=now,
        started_at=now,
        finished_at=now + timedelta(seconds=1),
        failure=FleetRunFailure.recovery("restart interrupted execution"),
        progress=FleetRunProgress(total=1, succeeded=0, failed=0, needs_attention=0),
    )
    detail = FleetRunDetail(
        repository_admissions=repository_admissions_for(run.target, run.targets),
        run=run,
        execution=DirectFleetExecutionAdmission(),
    )
    detail = detail.model_copy(
        update={
            "lifecycle_events": (
                CanonicalFleetJournalBuilder(detail)
                .run_started(run.started_at)
                .run_failed(run.failure, run.finished_at)
                .build()
            )
        }
    )

    tape = detail.game_tape

    assert tape.status is GameTapeStatus.INTERRUPTED
    assert tape.repositories[0].status is GameTapeStatus.NOT_RUN
    assert all(step.status is GameTapeStatus.NOT_RUN for step in tape.repositories[0].steps)
    assert tape.issue_count == 1
    assert tape.issue_focus[0].scope == "RUN"
    assert tape.issue_focus[0].repository_execution_id is None
    assert tape.issue_focus[0].step_execution_id is None
    assert tape.issue_focus[0].code == "RUN_INTERRUPTED_BY_RESTART"
    assert tape.issue_focus[0].message == "restart interrupted execution"
    assert tape.play_by_play[-1].event_type is GameTapeEventType.RUN_INTERRUPTED
    assert sum(
        event.event_type is GameTapeEventType.STEP_NOT_REACHED for event in tape.play_by_play
    ) == len(plan.steps)


def test_game_tape_uses_journal_sequence_for_equal_timestamp_parallel_repository_starts(
    tmp_path: Path,
) -> None:
    first = RepositoryIdentity(name="first", path=tmp_path / "first")
    second = RepositoryIdentity(name="second", path=tmp_path / "second")
    plan = _plan()
    target = _target(plan)
    now = utc_now()
    run = RunningFleetRun(
        status=FleetRunStatus.RUNNING,
        fleet_run_id="run-sequence-order",
        target=target,
        capability=capability_snapshot(OperationAction.EXECUTE_PLAN, target),
        repository_scope=fleet_repository_scope(None),
        targets=(first, second),
        accepted_at=now,
        started_at=now,
        progress=FleetRunProgress(total=2, succeeded=0, failed=0, needs_attention=0),
    )
    detail = FleetRunDetail(
        repository_admissions=repository_admissions_for(run.target, run.targets),
        run=run,
        execution=DirectFleetExecutionAdmission(),
    )
    first_operation = repository_attempt_operation_id(run.fleet_run_id, first.repository_id)
    second_operation = repository_attempt_operation_id(run.fleet_run_id, second.repository_id)
    detail = detail.model_copy(
        update={
            "lifecycle_events": (
                CanonicalFleetJournalBuilder(detail)
                .run_started(now)
                .repository_started(second.repository_id, second_operation, now)
                .repository_started(first.repository_id, first_operation, now)
                .build()
            )
        }
    )

    starts = [
        event.repository_id
        for event in detail.game_tape.play_by_play
        if event.event_type is GameTapeEventType.REPOSITORY_STARTED
    ]

    assert starts == [second.repository_id, first.repository_id]


def test_game_tape_diff_compares_only_debugging_facts(tmp_path: Path) -> None:
    repository = RepositoryIdentity(path=tmp_path / "repo")
    plan = _plan()
    target = _target(plan)
    now = utc_now()

    def detail(run_id: str, duration_ms: int, returncode: int) -> FleetRunDetail:
        step = (
            CompletedPlanStepExecution(
                step_id="prepare",
                kind="command",
                started_at=now,
                finished_at=now + timedelta(milliseconds=duration_ms),
                argv=("echo", "prepare"),
                returncode=0,
                stdout_evidence=_output("ready\n"),
                stderr_evidence=_output(""),
                duration_ms=duration_ms,
                repository_state_before="before",
                repository_state_after="after",
            )
            if returncode == 0
            else FailedPlanStepExecution(
                step_id="prepare",
                kind="command",
                started_at=now,
                finished_at=now + timedelta(milliseconds=duration_ms),
                argv=("echo", "prepare", "changed"),
                returncode=returncode,
                stdout_evidence=_output("failed\n"),
                stderr_evidence=_output("trace\n"),
                duration_ms=duration_ms,
                repository_state_before="before",
                repository_state_after="before",
            )
        )
        result = FleetPlanResult(plan_id=plan.plan_id, steps=(step,))
        verdict = Verdict.READY if returncode == 0 else Verdict.FAILED
        report = make_operation_report(
            repository,
            OperationAction.EXECUTE_PLAN,
            verdict,
            ReasonCode.READY if returncode == 0 else ReasonCode.TESTS_FAILED,
            fleet_run_id=run_id,
            target=target,
            returncode=returncode,
            evidence=FleetPlanOperationEvidence(kind="FLEET_PLAN", plan=result),
        )
        report = report.model_copy(
            update={
                "provenance": report.provenance.model_copy(
                    update={
                        "started_at": now,
                        "finished_at": now + timedelta(milliseconds=duration_ms),
                    }
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
            progress=FleetRunProgress(
                total=1,
                succeeded=1 if returncode == 0 else 0,
                failed=1 if returncode != 0 else 0,
                needs_attention=1 if returncode != 0 else 0,
            ),
        )
        return FleetRunDetail(
            repository_admissions=repository_admissions_for(run.target, run.targets),
            run=run,
            execution=DirectFleetExecutionAdmission(),
            operations=(report,),
        )

    base = detail("run-base", 1000, 0)
    compared = detail("run-compared", 2500, 1)
    diff = compare_game_tapes(base.game_tape, compared.game_tape)

    assert diff.plan_changed is False
    assert diff.duration_delta_ms == 1500
    assert len(diff.repositories) == 1
    repository_diff = diff.repositories[0]
    assert repository_diff.workspace_changed is False
    assert repository_diff.runtime_changed is False
    assert repository_diff.toolchain_changed is False
    assert repository_diff.isolation_changed is False
    step = repository_diff.steps[0]
    assert step.base_status is GameTapeStatus.PASSED
    assert step.compared_status is GameTapeStatus.FAILED
    assert step.duration_delta_ms == 1500
    assert step.command_changed is True
    assert step.provenance_changed is False
    assert step.base_attempt_count == 0
    assert step.compared_attempt_count == 0
    assert step.attempt_count_changed is False
    assert step.tool_changed is False
    assert step.egress_changed is False
    assert step.resource_policy_changed is False
    assert step.failure_signature_changed is True


def test_game_tape_compares_adjacent_attempts_from_canonical_evidence() -> None:
    first = _failed_attempt().model_copy(
        update={
            "retry": ScheduledPlanRetryDecisionEvidence(
                exit_code=1,
                policy_reason="explicit retry",
                delay_seconds=1,
                next_attempt=2,
            )
        }
    )
    second = _failed_attempt().model_copy(
        update={
            "attempt": 2,
            "duration_ms": 4500,
            "repository_state_before": first.repository_state_after,
            "tool": first.tool.model_copy(update={"sha256": "2" * 64}) if first.tool else None,
            "stdout_evidence": _output("failed again\n"),
        }
    )

    from oh_goon.core.game_tape_attempt_comparison import compare_adjacent_attempts

    transition = compare_adjacent_attempts((first, second))[0]

    assert transition.from_attempt == 1
    assert transition.to_attempt == 2
    assert transition.retry_linked is True
    assert transition.duration_delta_ms == 1500
    assert transition.repository_state_continuity is True
    assert transition.tool_changed is True
    assert transition.stdout_changed is True
    assert transition.stderr_changed is False
    assert transition.egress_changed is False
    assert transition.resource_policy_changed is False


@pytest.mark.parametrize(
    ("authored_timeout_seconds", "selected_timeout_seconds", "elapsed_ms"),
    ((1, 1, 1000), (3600, 300, 310_000)),
)
def test_game_tape_preserves_timeout_without_inventing_the_effective_budget(
    authored_timeout_seconds: int, selected_timeout_seconds: int, elapsed_ms: int
) -> None:
    from oh_goon.core.execution_ledger import _step_terminal_message
    from oh_goon.core.game_tape_step_projection import project_steps
    from oh_goon.core.models.fleet_plan_step_primitives import PLAN_TIMEOUT_RETURN_CODE

    now = utc_now()
    planned = ResolvedCommandStep(
        step_id="timeout-step",
        argv=("python", "-c", "sleep"),
        timeout_seconds=authored_timeout_seconds,
    )
    execution = FailedPlanStepExecution(
        step_id="timeout-step",
        kind="command",
        started_at=now,
        finished_at=now + timedelta(milliseconds=elapsed_ms),
        argv=("python", "-c", "sleep"),
        returncode=PLAN_TIMEOUT_RETURN_CODE,
        stdout_evidence=_output(""),
        stderr_evidence=_output(
            f"Fleet plan step timed out after {selected_timeout_seconds} seconds\n"
        ),
        duration_ms=elapsed_ms,
        repository_state_before="before",
        repository_state_after="before",
        settlement=PlanStepSettlementEvidence(
            outcome="TIMED_OUT", cleanup="VERIFIED", reason="DEADLINE_EXPIRED"
        ),
    )
    projected = project_steps(
        "run-timeout",
        "repo-timeout",
        (planned,),
        (execution,),
        step_sources={},
        source_label="goon.yml",
        terminal=True,
        operation_reason_code="OPERATION_FAILED",
    )
    # The authored limit can exceed the selected cap; elapsed time also includes
    # cleanup. Neither is a recorded effective timeout budget in this projection.
    assert projected[0].reason == "Step timed out."
    assert projected[0].status is GameTapeStatus.FAILED
    assert projected[0].duration_ms == elapsed_ms
    assert projected[0].settlement == execution.settlement
    assert projected[0].failure is not None
    assert projected[0].failure.exit_code == PLAN_TIMEOUT_RETURN_CODE
    assert projected[0].failure.reason_code == "OPERATION_FAILED"
    assert projected[0].failure.evidence_sha256 == execution.stderr_evidence.sha256
    assert projected[0].stderr_evidence == execution.stderr_evidence
    assert _step_terminal_message(GameTapeStatus.FAILED, execution) == "Step timed out."


def test_execution_ledger_rejects_non_terminal_status_at_terminal_boundary() -> None:
    from oh_goon.core.execution_ledger import (
        _repository_terminal_message,
        _repository_terminal_type,
        _run_terminal_message,
        _run_terminal_type,
        _step_terminal_type,
    )

    with pytest.raises(AssertionError, match="Non-terminal Run status"):
        _run_terminal_type(GameTapeStatus.RUNNING)
    with pytest.raises(AssertionError, match="Non-terminal Run status"):
        _run_terminal_message(GameTapeStatus.ADMITTED)
    with pytest.raises(AssertionError, match="Non-terminal Repository status"):
        _repository_terminal_type(GameTapeStatus.RUNNING)
    with pytest.raises(AssertionError, match="Non-terminal Repository status"):
        _repository_terminal_message(GameTapeStatus.NOT_RUN)
    with pytest.raises(AssertionError, match="Non-terminal Step status"):
        _step_terminal_type(GameTapeStatus.RUNNING)


def test_game_tape_lifecycle_rejects_unprojectable_terminal_facts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    import oh_goon.core.game_tape_lifecycle as lifecycle

    terminal_run = SimpleNamespace(status=FleetRunStatus.COMPLETED, failure=None)
    monkeypatch.setattr(lifecycle, "fleet_run_outcome", lambda _run: FleetRunOutcome.IN_PROGRESS)
    with pytest.raises(AssertionError, match="Non-terminal Fleet Run outcome"):
        lifecycle.run_status(terminal_run)  # type: ignore[arg-type]

    unknown_report = SimpleNamespace(outcome=SimpleNamespace(verdict=Verdict.UNKNOWN))
    with pytest.raises(AssertionError, match="Unsupported repository verdict"):
        lifecycle.repository_status(unknown_report, terminal=True)  # type: ignore[arg-type]

    with pytest.raises(AssertionError, match="Unsupported step execution"):
        lifecycle.step_status(object(), terminal=True)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "changed",
    (
        {"host_and_external_ip_networking": "host_unrestricted"},
        {"scoped_write_roots": ("src",)},
        {"credential_visibility": "codex_auth_file_read_write"},
    ),
)
def test_game_tape_attempt_comparison_retains_physical_scope_changes(
    changed: dict[str, object],
) -> None:
    from oh_goon.core.game_tape_attempt_comparison import compare_adjacent_attempts

    boundary = PlanStandardIsolationEvidence(
        backend=PlanIsolationBackendEvidence(
            backend_id="bubblewrap",
            configuration_id="selected-backend",
            kernel_boundary="shared_host_kernel",
            remote_execution=False,
            experimental=False,
        ),
        workspace_read_only=True,
        broker_socket_mounted=False,
        resource_owner="none",
    )
    first = _failed_attempt().model_copy(update={"standard_isolation": boundary})
    second = first.model_copy(
        update={"attempt": 2, "standard_isolation": boundary.model_copy(update=changed)}
    )

    assert compare_adjacent_attempts((first, second))[0].isolation_changed is True


@pytest.mark.parametrize("boundary", ["cancel", "unknown", "missing_terminal", "ongoing"])
@pytest.mark.parametrize("clock", ["equal", "inverted"])
def test_unreported_step_lifecycle_uses_canonical_facts_without_inventing_results(
    tmp_path: Path, boundary: str, clock: str
) -> None:
    repository = RepositoryIdentity(path=tmp_path / "repo")
    plan = ResolvedFleetPlan.create(
        name="interrupted-phase",
        description="",
        execution=MaintenanceExecutionPolicy(),
        steps=tuple(
            ResolvedCommandStep(step_id=name, argv=("true",))
            for name in ("prepare", "native", "later")
        ),
    )
    target = _target(plan)
    now = utc_now()
    common: dict[str, Any] = dict(
        fleet_run_id="run-phase",
        target=target,
        capability=capability_snapshot(OperationAction.EXECUTE_PLAN, target),
        repository_scope=fleet_repository_scope(None),
        targets=(repository,),
        accepted_at=now,
        started_at=now,
        progress=FleetRunProgress(total=1, succeeded=0, failed=0, needs_attention=0),
    )
    run: FleetRunState
    if boundary == "ongoing":
        run = RunningFleetRun(status=FleetRunStatus.RUNNING, **common)
    elif boundary == "cancel":
        run = CancelledFleetRun(
            status=FleetRunStatus.CANCELLED,
            cancel_requested_at=now,
            finished_at=now,
            **common,
        )
    else:
        run = FailedFleetRun(
            status=FleetRunStatus.FAILED,
            finished_at=now,
            failure=FleetRunFailure.uncertain_repository_effects(1),
            **common,
        )
    detail = FleetRunDetail(
        run=run,
        execution=DirectFleetExecutionAdmission(),
        repository_admissions=repository_admissions_for(target, (repository,)),
    )
    operation_id = repository_attempt_operation_id(run.fleet_run_id, repository.repository_id)
    events = list(
        CanonicalFleetJournalBuilder(detail)
        .run_started(now)
        .repository_started(repository.repository_id, operation_id, now)
        .step_reached(repository.repository_id, operation_id, "prepare", 1, now)
        .step_started(repository.repository_id, operation_id, "prepare", 1, now)
        .step_succeeded(repository.repository_id, operation_id, "prepare", 1, 0, now)
        .step_reached(repository.repository_id, operation_id, "native", 2, now)
        .step_started(repository.repository_id, operation_id, "native", 2, now)
        .step_attempt_created(repository.repository_id, operation_id, "native", 2, 1, now)
        .build()
    )
    physical: dict[str, Any] = dict(
        fleet_run_id=run.fleet_run_id,
        repository_id=repository.repository_id,
        operation_id=operation_id,
        step_id="native",
        step_order=2,
        attempt_number=1,
        step_attempt_id="attempt-native",
        physical_execution_id="physical-native",
        pid=4321,
        process_identity="original-process",
    )
    events.append(
        ProcessStartedEvent(
            event_type=FleetRunEventType.PROCESS_STARTED,
            sequence=len(events) + 1,
            generated_at=now,
            **physical,
        )
    )
    later_clock = now if clock == "equal" else now - timedelta(seconds=1)
    if boundary == "cancel":
        events.append(
            StepCancelledEvent(
                event_type=FleetRunEventType.STEP_CANCELLED,
                sequence=len(events) + 1,
                generated_at=later_clock,
                fleet_run_id=run.fleet_run_id,
                repository_id=repository.repository_id,
                operation_id=operation_id,
                step_id="native",
                step_order=2,
            )
        )
        # A later physical closure fact does not re-open the closed Step.
        events.append(
            DescendantsQuiescentEvent(
                event_type=FleetRunEventType.DESCENDANTS_QUIESCENT,
                sequence=len(events) + 1,
                generated_at=now,
                **physical,
            )
        )
        events.append(
            RunCancelledEvent(
                event_type=FleetRunEventType.RUN_CANCELLED,
                sequence=len(events) + 1,
                generated_at=now,
                fleet_run_id=run.fleet_run_id,
            )
        )
    elif boundary in ("unknown", "missing_terminal"):
        if boundary == "unknown":
            events.append(
                ProcessStateUnknownEvent(
                    event_type=FleetRunEventType.PROCESS_STATE_UNKNOWN,
                    sequence=len(events) + 1,
                    generated_at=later_clock,
                    reason="Original controller stopped without terminal evidence",
                    **physical,
                )
            )
        assert isinstance(run, FailedFleetRun)
        events.append(
            RunFailedEvent(
                event_type=FleetRunEventType.RUN_FAILED,
                sequence=len(events) + 1,
                generated_at=now,
                fleet_run_id=run.fleet_run_id,
                failure=run.failure,
            )
        )
    # Sequence, not tuple order or a wall-clock comparison, owns the observation.
    detail = detail.model_copy(update={"lifecycle_events": tuple(reversed(events))})
    tape = detail.game_tape
    projected = tape.repositories[0]
    expected = (
        GameTapeStatus.CANCELLED
        if boundary == "cancel"
        else GameTapeStatus.RUNNING
        if boundary == "ongoing"
        else GameTapeStatus.INTERRUPTED
    )
    assert projected.status is expected
    assert [step.status for step in projected.steps] == [
        GameTapeStatus.PASSED,
        expected,
        GameTapeStatus.PLANNED if boundary == "ongoing" else GameTapeStatus.NOT_RUN,
    ]
    assert all(
        step.attempts == () and step.exit_code is None and step.stdout_evidence is None
        for step in projected.steps
    )
    assert all(step.finished_at is None for step in projected.steps)
    not_reached = [
        event.step_id
        for event in tape.play_by_play
        if event.event_type is GameTapeEventType.STEP_NOT_REACHED
    ]
    assert not_reached == ([] if boundary == "ongoing" else ["later"])
    assert not any(
        event.event_type is GameTapeEventType.REPOSITORY_NOT_RUN for event in tape.play_by_play
    )
    if boundary == "cancel":
        assert any(
            event.event_type is GameTapeEventType.STEP_CANCELLED for event in tape.play_by_play
        )
    if boundary == "unknown":
        assert (
            projected.runtime_progress is not None
            and projected.runtime_progress.process_state == "UNKNOWN"
        )
    assert detail.operations == ()
