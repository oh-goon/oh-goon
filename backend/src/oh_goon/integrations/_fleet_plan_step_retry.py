from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from oh_goon.core.fleet_plan_retry import retry_decision
from oh_goon.core.models.fleet_plan import ResolvedCommandStep, ResolvedPlanStep, StepFailurePolicy
from oh_goon.core.models.fleet_plan_evidence import (
    PlanStepAttemptEvidence,
    PublishedPlanOutputEvidence,
)
from oh_goon.core.models.fleet_plan_retry import (
    PlanRetryDecisionEvidence,
    ScheduledPlanRetryDecisionEvidence,
)
from oh_goon.core.ports.step_output import StepOutputObserver
from oh_goon.engine.git import GitObservation
from oh_goon.integrations._fleet_plan_step_outcome import (
    StepOutcome,
    step_succeeded,
    wait_for_retry,
)
from oh_goon.integrations.fleet_plan_attempt_evidence import (
    build_direct_attempt_evidence,
    build_strict_attempt_evidence,
)
from oh_goon.integrations.fleet_plan_run import (
    FleetPlanStepAttempt,
    PreparedPlanRun,
    SealedAttemptOutputs,
)
from oh_goon.integrations.fleet_plan_step_execution import (
    StepRuntime,
    execute_direct_step,
    execute_strict_step,
)
from oh_goon.integrations.fleet_plan_step_publication import (
    AttemptPublication,
    publish_direct_attempt_outputs,
    publish_strict_attempt_outputs,
)


@dataclass(frozen=True, slots=True)
class RetryExecutionResult:
    """Named result of one step's complete retry lifecycle."""

    outcome: StepOutcome
    after: GitObservation
    attempts: tuple[PlanStepAttemptEvidence, ...]
    outputs: dict[str, str]
    output_evidence: tuple[PublishedPlanOutputEvidence, ...]
    evidence_repository: Path | None


@dataclass(frozen=True, slots=True)
class _PhysicalAttemptResult:
    """One already-resolved physical attempt before retry policy is attached."""

    outcome: StepOutcome
    after: GitObservation
    evidence: PlanStepAttemptEvidence | None
    outputs: dict[str, str]
    output_evidence: tuple[PublishedPlanOutputEvidence, ...]
    evidence_repository: Path


PhysicalAttemptExecutor = Callable[[int, GitObservation], _PhysicalAttemptResult]


def execute_direct_with_retries(
    repository: Path,
    step: ResolvedPlanStep,
    canonical_step: ResolvedPlanStep,
    runtime: StepRuntime,
    initial_observation: GitObservation,
    output_observer: StepOutputObserver | None = None,
    *,
    step_order: int,
) -> RetryExecutionResult:
    """Run retries against the live repository using direct host execution only."""

    def execute_attempt(attempt_number: int, previous: GitObservation) -> _PhysicalAttemptResult:
        outcome = execute_direct_step(
            repository,
            step,
            runtime,
            attempt_number=attempt_number,
            step_order=step_order,
            output_observer=output_observer,
        )
        after = runtime.git.observe(repository, runtime.cancel_requested)
        publication = _successful_direct_publication(step, outcome, repository)
        outcome = publication.outcome
        evidence = None
        if not outcome.skipped:
            evidence = build_direct_attempt_evidence(
                canonical_step,
                step,
                attempt_number,
                outcome,
                repository,
                previous,
                after,
                retry=None,
            )
        return _PhysicalAttemptResult(
            outcome=outcome,
            after=after,
            evidence=evidence,
            outputs=publication.outputs,
            output_evidence=publication.output_evidence,
            evidence_repository=repository,
        )

    return _execute_with_retries(
        step, runtime, initial_observation, execute_attempt, step_order=step_order
    )


def execute_strict_with_retries(
    repository: Path,
    step: ResolvedPlanStep,
    canonical_step: ResolvedPlanStep,
    prepared_run: PreparedPlanRun,
    runtime: StepRuntime,
    initial_observation: GitObservation,
    output_observer: StepOutputObserver | None = None,
    *,
    step_order: int,
) -> RetryExecutionResult:
    """Run retries only through attempts created by one prepared strict run."""

    def execute_attempt(attempt_number: int, previous: GitObservation) -> _PhysicalAttemptResult:
        attempt = prepared_run.create_attempt(step, attempt_number)
        attempt_repository = attempt.paths.workspace
        before = runtime.git.observe(attempt_repository, runtime.cancel_requested)
        outcome = execute_strict_step(
            attempt_repository,
            step,
            attempt,
            prepared_run.process_isolation,
            runtime,
            attempt_number=attempt_number,
            step_order=step_order,
            output_observer=output_observer,
        )
        sealed_outputs = attempt.seal_owned_outputs()
        after = runtime.git.observe(attempt_repository, runtime.cancel_requested)
        publication = _successful_strict_publication(
            step, outcome, attempt_repository, prepared_run, attempt, sealed_outputs
        )
        outcome = publication.outcome
        evidence = None
        if not outcome.skipped:
            evidence = build_strict_attempt_evidence(
                canonical_step,
                step,
                attempt_number,
                outcome,
                attempt_repository,
                before,
                after,
                attempt=attempt,
                cache_output_identity=publication.cache_identity,
                retained_cache=publication.retained_cache,
                retry=None,
            )
        return _PhysicalAttemptResult(
            outcome=outcome,
            after=after,
            evidence=evidence,
            outputs=publication.outputs,
            output_evidence=publication.output_evidence,
            evidence_repository=attempt_repository,
        )

    return _execute_with_retries(
        step, runtime, initial_observation, execute_attempt, step_order=step_order
    )


def _execute_with_retries(
    step: ResolvedPlanStep,
    runtime: StepRuntime,
    initial_observation: GitObservation,
    execute_attempt: PhysicalAttemptExecutor,
    *,
    step_order: int,
) -> RetryExecutionResult:
    """Own retry progression after direct/strict physical execution was selected."""

    policy = step.retry
    attempts: list[PlanStepAttemptEvidence] = []
    after = initial_observation
    outcome: StepOutcome | None = None
    published_values: dict[str, str] = {}
    published_evidence: tuple[PublishedPlanOutputEvidence, ...] = ()
    evidence_repository: Path | None = None
    for attempt_number in range(1, policy.max_attempts + 1):
        observer = runtime.lifecycle_observer
        if observer is not None:
            observer(
                step.step_id,
                step_order,
                "STEP_ATTEMPT_CREATED",
                attempt_number=attempt_number,
            )
        physical = execute_attempt(attempt_number, after)
        outcome = physical.outcome
        after = physical.after
        evidence_repository = physical.evidence_repository
        published_values = physical.outputs
        published_evidence = physical.output_evidence
        succeeded = step_succeeded(step, outcome)
        decision: PlanRetryDecisionEvidence | None = None
        if not outcome.skipped and not succeeded:
            decision = retry_decision(
                policy, attempt_number=attempt_number, exit_code=outcome.returncode
            )
        if physical.evidence is not None:
            attempts.append(physical.evidence.model_copy(update={"retry": decision}))
        if outcome.skipped or succeeded:
            break
        if not _wait_for_scheduled_retry(decision, runtime.cancel_requested):
            break
    assert outcome is not None
    return RetryExecutionResult(
        outcome=outcome,
        after=after,
        attempts=tuple(attempts),
        outputs=published_values,
        output_evidence=published_evidence,
        evidence_repository=evidence_repository,
    )


def _successful_direct_publication(
    step: ResolvedPlanStep,
    outcome: StepOutcome,
    repository: Path,
) -> AttemptPublication:
    if outcome.skipped:
        return AttemptPublication({}, (), outcome, None, None)
    if not step_succeeded(step, outcome):
        # Explicitly continued, single-attempt direct command Steps may transport
        # a bounded diagnostic file to subsequent authored Steps.  Its nonzero
        # physical result remains FAILED in Game Tape.  Agent proposals, strict
        # workspaces, and retryable attempts never use this diagnostic path.
        if not (
            type(step) is ResolvedCommandStep
            and step.on_failure is StepFailurePolicy.CONTINUE
            and step.retry.max_attempts == 1
            and step.outputs
        ):
            return AttemptPublication({}, (), outcome, None, None)
    return publish_direct_attempt_outputs(step, outcome, repository)


def _successful_strict_publication(
    step: ResolvedPlanStep,
    outcome: StepOutcome,
    repository: Path,
    prepared_run: PreparedPlanRun,
    attempt: FleetPlanStepAttempt,
    sealed_outputs: SealedAttemptOutputs,
) -> AttemptPublication:
    if outcome.skipped or not step_succeeded(step, outcome):
        return AttemptPublication({}, (), outcome, None, None)
    return publish_strict_attempt_outputs(
        step, outcome, repository, prepared_run, attempt, sealed_outputs
    )


def _wait_for_scheduled_retry(
    decision: PlanRetryDecisionEvidence | None,
    cancel_requested: Callable[[], bool] | None,
) -> bool:
    if not isinstance(decision, ScheduledPlanRetryDecisionEvidence):
        return False
    wait_for_retry(decision.delay_seconds, cancel_requested)
    return True
