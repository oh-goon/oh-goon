from __future__ import annotations

from datetime import datetime
from typing import Literal

from oh_goon.core.game_tape_attempt_comparison import compare_adjacent_attempts
from oh_goon.core.game_tape_identity import step_execution_id
from oh_goon.core.game_tape_lifecycle import step_status
from oh_goon.core.models.fleet_observation import NativeAgentObservation
from oh_goon.core.models.fleet_plan import (
    ResolvedAgentStep,
    ResolvedPlanStep,
    ResolvedStepSource,
)
from oh_goon.core.models.fleet_plan_evidence import (
    FailedPlanStepTeardownEvidence,
    PlanArtifactEvidence,
    PlanStepExecution,
    PublishedPlanOutputEvidence,
    SkippedPlanStepExecution,
)
from oh_goon.core.models.fleet_plan_step_primitives import (
    PLAN_EXECUTABLE_NOT_FOUND_RETURN_CODE,
    PLAN_EXECUTABLE_NOT_RUNNABLE_RETURN_CODE,
    PLAN_TIMEOUT_RETURN_CODE,
    PathExistsCondition,
    PathMissingCondition,
    StepStatusCondition,
)
from oh_goon.core.models.fleet_plan_values import (
    HostEnvironmentPart,
    LiteralEnvironmentPart,
    ResolvedEnvironmentPart,
)
from oh_goon.core.models.game_tape import (
    GameTapeEnvironmentSource,
    GameTapeFailureFingerprint,
    GameTapeSourceLocation,
    GameTapeStepExecution,
    GameTapeWhy,
)
from oh_goon.core.models.run_events import StepStartedEvent
from oh_goon.core.product_identity import stable_id
from oh_goon.core.vocabulary import GameTapeStatus


def project_steps(
    fleet_run_id: str,
    repository_id: str,
    planned_steps: tuple[ResolvedPlanStep, ...],
    executions: tuple[PlanStepExecution, ...],
    *,
    step_sources: dict[str, ResolvedStepSource],
    source_label: str,
    terminal: bool,
    operation_reason_code: str | None,
    live_started_at: dict[str, datetime] | None = None,
    lifecycle_events: tuple[object, ...] = (),
) -> tuple[GameTapeStepExecution, ...]:
    by_id = {item.step_id: item for item in executions}
    events_by_step: dict[str, list[object]] = {}
    for event in lifecycle_events:
        if (
            getattr(event, "fleet_run_id", None) == fleet_run_id
            and getattr(event, "repository_id", None) == repository_id
            and isinstance(step_id := getattr(event, "step_id", None), str)
        ):
            events_by_step.setdefault(step_id, []).append(event)
    projected: list[GameTapeStepExecution] = []
    for order, step in enumerate(planned_steps, start=1):
        execution = by_id.get(step.step_id)
        events = tuple(events_by_step.get(step.step_id, ()))
        starts = [event for event in events if isinstance(event, StepStartedEvent)]
        live_start = (
            min(starts, key=lambda event: event.sequence).generated_at
            if starts
            else (live_started_at or {}).get(step.step_id)
        )
        status = step_status(
            execution, terminal=terminal, started=live_start is not None, lifecycle_events=events
        )
        previous = projected[-1] if projected else None
        projected.append(
            _step_execution(
                fleet_run_id,
                repository_id,
                step,
                order,
                status,
                execution,
                previous,
                source_location=step_sources.get(step.step_id),
                source_label=source_label,
                operation_reason_code=operation_reason_code,
                live_started_at=live_start,
            )
        )
    return tuple(projected)


def _step_execution(
    fleet_run_id: str,
    repository_id: str,
    step: ResolvedPlanStep,
    order: int,
    status: GameTapeStatus,
    execution: PlanStepExecution | None,
    previous: GameTapeStepExecution | None,
    *,
    source_location: ResolvedStepSource | None,
    source_label: str,
    operation_reason_code: str | None,
    live_started_at: datetime | None = None,
) -> GameTapeStepExecution:
    started_at = execution.started_at if execution is not None else live_started_at
    finished_at = execution.finished_at if execution is not None else None
    returncode = getattr(execution, "returncode", None) if execution is not None else None
    transitions = compare_adjacent_attempts(execution.attempts) if execution is not None else ()
    return GameTapeStepExecution(
        step_execution_id=step_execution_id(fleet_run_id, repository_id, step.step_id),
        step_id=step.step_id,
        kind=step.kind,
        order=order,
        status=status,
        started_at=started_at,
        finished_at=finished_at,
        duration_ms=execution.duration_ms if execution is not None else None,
        command=execution.argv if execution is not None else _planned_argv(step),
        working_directory=getattr(step, "working_directory", "."),
        environment=_environment_sources(step),
        exit_code=returncode,
        **({"settlement": execution.settlement} if execution is not None else {}),
        attempts=execution.attempts if execution is not None else (),
        attempt_transitions=transitions,
        stdout_evidence=execution.stdout_evidence if execution is not None else None,
        stderr_evidence=execution.stderr_evidence if execution is not None else None,
        output_names=_output_names(step, execution),
        artifacts=_artifacts(execution),
        outputs=_published_outputs(execution),
        native_agent_observations=(
            tuple(
                observation
                for observation in execution.observations
                if isinstance(observation, NativeAgentObservation)
            )
            if execution is not None
            else ()
        ),
        reason=(
            execution.reason
            if isinstance(execution, SkippedPlanStepExecution)
            else _failure_reason(step, status, returncode, execution)
        ),
        why=_why(
            step,
            order,
            status,
            previous,
            source_location=source_location,
            source_label=source_label,
        ),
        failure=_failure_fingerprint(execution, operation_reason_code)
        if status is GameTapeStatus.FAILED and returncode is not None
        else None,
        execution_authority=(
            execution.attempts[-1].execution_authority
            if execution is not None
            and execution.attempts
            and execution.attempts[-1].execution_authority is not None
            else None
        ),
    )


def _failure_reason(
    step: ResolvedPlanStep,
    status: GameTapeStatus,
    returncode: int | None,
    execution: PlanStepExecution | None,
) -> str | None:
    if status is not GameTapeStatus.FAILED:
        return None
    teardown = execution.teardown if execution is not None else None
    if isinstance(teardown, FailedPlanStepTeardownEvidence):
        return f"Step teardown failed: {teardown.reason_code}."
    if returncode in {
        PLAN_EXECUTABLE_NOT_RUNNABLE_RETURN_CODE,
        PLAN_EXECUTABLE_NOT_FOUND_RETURN_CODE,
    }:
        native = isinstance(step, ResolvedAgentStep) and step.workload.harness == "native"
        argv = execution.argv if native and execution is not None else _planned_argv(step)
        if native and not argv:
            return (
                "Executable is not runnable."
                if returncode == PLAN_EXECUTABLE_NOT_RUNNABLE_RETURN_CODE
                else "Executable not found."
            )
        executable = argv[0] if argv else "declared executable"
        if returncode == PLAN_EXECUTABLE_NOT_RUNNABLE_RETURN_CODE:
            return f"Executable is not runnable: {executable}."
        return f"Executable not found: {executable}."
    if returncode != PLAN_TIMEOUT_RETURN_CODE:
        return None
    timeout = getattr(step, "timeout_seconds", None)
    if isinstance(timeout, (int, float)):
        rendered = f"{timeout:g}"
        return f"Step timed out after {rendered} seconds."
    return "Step timed out."


def _failure_fingerprint(
    execution: PlanStepExecution | None,
    operation_reason_code: str | None,
) -> GameTapeFailureFingerprint | None:
    if execution is None:
        return None
    returncode = getattr(execution, "returncode", None)
    if returncode is None:
        return None
    teardown = execution.teardown
    effective_reason_code = (
        f"STEP_TEARDOWN_{teardown.reason_code}"
        if isinstance(teardown, FailedPlanStepTeardownEvidence)
        else operation_reason_code
    )
    attempt = execution.attempts[-1] if execution.attempts else None
    stdout = attempt.stdout_evidence if attempt is not None else execution.stdout_evidence
    stderr = attempt.stderr_evidence if attempt is not None else execution.stderr_evidence
    evidence_stream: Literal["stdout", "stderr"] | None = None
    evidence = None
    if stderr.byte_count > 0:
        evidence_stream, evidence = "stderr", stderr
    elif stdout.byte_count > 0:
        evidence_stream, evidence = "stdout", stdout
    signature_id = stable_id(
        "game-tape-failure",
        {
            "exit_code": returncode,
            "reason_code": effective_reason_code,
            "stream": evidence_stream,
            "sha256": evidence.sha256 if evidence is not None else None,
        },
    )
    return GameTapeFailureFingerprint(
        exit_code=returncode,
        reason_code=effective_reason_code,
        attempt=attempt.attempt if attempt is not None else None,
        signature_id=signature_id,
        evidence_stream=evidence_stream,
        evidence_sha256=evidence.sha256 if evidence is not None else None,
        evidence_line_count=evidence.line_count if evidence is not None else None,
        evidence_byte_count=evidence.byte_count if evidence is not None else None,
    )


def _planned_argv(step: ResolvedPlanStep) -> tuple[str, ...]:
    if isinstance(step, ResolvedAgentStep) and step.workload.harness == "native":
        selected = step.workload.native_agent.definition
        return (selected.executable, *selected.arguments) if selected.executable is not None else ()
    argv = getattr(step, "argv", ())
    if argv:
        return tuple(argv)
    executable = getattr(step, "executable", None)
    args = getattr(step, "args", ())
    return (executable, *args) if isinstance(executable, str) else ()


def _declared_outputs(step: ResolvedPlanStep) -> tuple[str, ...]:
    return tuple(item.name for item in getattr(step, "outputs", ()))


def _output_names(step: ResolvedPlanStep, execution: PlanStepExecution | None) -> tuple[str, ...]:
    if execution is None:
        return _declared_outputs(step)
    return tuple(item.name for item in execution.outputs)


def _artifacts(execution: PlanStepExecution | None) -> tuple[PlanArtifactEvidence, ...]:
    return () if execution is None else execution.artifacts


def _published_outputs(
    execution: PlanStepExecution | None,
) -> tuple[PublishedPlanOutputEvidence, ...]:
    return () if execution is None else execution.outputs


def _why(
    step: ResolvedPlanStep,
    order: int,
    status: GameTapeStatus,
    previous: GameTapeStepExecution | None,
    *,
    source_location: ResolvedStepSource | None,
    source_label: str,
) -> GameTapeWhy:
    reached = status not in {GameTapeStatus.PLANNED, GameTapeStatus.NOT_RUN}
    if reached:
        explanation = "Execution reached this declared step in plan order."
    elif previous is not None and previous.status is GameTapeStatus.FAILED:
        explanation = f"Not reached after {previous.step_id} failed."
    elif previous is not None:
        explanation = (
            f"Not reached after {previous.step_id}; execution ended before this step started."
        )
    else:
        explanation = "The step is in the admitted plan but execution never started it."
    location = (
        GameTapeSourceLocation(
            source=source_location.source,
            line=source_location.line,
            column=source_location.column,
        )
        if source_location is not None
        else None
    )
    declaration = (
        f"{source_location.source}:{source_location.line}"
        if source_location is not None
        else f"{source_label} · resolved step {order}"
    )
    return GameTapeWhy(
        declaration=declaration,
        source_location=location,
        previous_step_id=previous.step_id if previous is not None else None,
        previous_step_status=previous.status if previous is not None else None,
        condition=_condition_description(getattr(step, "when", None)),
        reached=reached,
        explanation=explanation,
    )


def _condition_description(condition: object | None) -> str | None:
    if isinstance(condition, PathExistsCondition):
        return f"path exists: {condition.path}"
    if isinstance(condition, PathMissingCondition):
        return f"path missing: {condition.path}"
    if isinstance(condition, StepStatusCondition):
        return f"{condition.step_id} status in {', '.join(condition.statuses)}"
    return None


def _environment_sources(step: ResolvedPlanStep) -> tuple[GameTapeEnvironmentSource, ...]:
    bindings = getattr(step, "environment", ())
    return tuple(
        GameTapeEnvironmentSource(
            name=binding.name,
            sources=tuple(_environment_part_source(part) for part in binding.parts),
        )
        for binding in bindings
    )


def _environment_part_source(part: ResolvedEnvironmentPart) -> str:
    if isinstance(part, HostEnvironmentPart):
        return f"host:{part.name}"
    if isinstance(part, LiteralEnvironmentPart):
        return "literal"
    raise TypeError(f"Unsupported resolved environment part: {type(part).__name__}")
