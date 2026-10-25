from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.fleet_plan import ResolvedPlanStep, StepFailurePolicy
from oh_goon.core.models.fleet_plan_evidence import (
    CompletedPlanStepExecution,
    FailedPlanStepExecution,
    FailedPlanStepTeardownEvidence,
    PlanArtifactEvidence,
    PlanStepAttemptEvidence,
    PlanStepExecution,
    PublishedPlanOutputEvidence,
)
from oh_goon.core.ports.step_output import StepOutputObserver
from oh_goon.core.time import utc_now
from oh_goon.engine.errors import OperationCancelledError
from oh_goon.engine.git import GitObservation
from oh_goon.integrations._fleet_plan_step_dataflow import (
    bind_step_outputs,
    condition_skip_reason,
)
from oh_goon.integrations._fleet_plan_step_evidence import step_evidence
from oh_goon.integrations._fleet_plan_step_outcome import (
    StepOutcome,
    failure_policy,
    is_finalizer,
    output_contract_failure,
    process_boundary_settled,
    skipped_after_failure,
)
from oh_goon.integrations._fleet_plan_step_retry import (
    RetryExecutionResult,
    execute_direct_with_retries,
    execute_strict_with_retries,
)
from oh_goon.integrations.fleet_plan_run import PreparedPlanRun
from oh_goon.integrations.fleet_plan_secret_redaction import redact_host_secrets
from oh_goon.integrations.fleet_plan_step_execution import StepRuntime
from oh_goon.integrations.fleet_plan_step_publication import (
    collect_direct_artifacts,
    collect_strict_artifacts,
)

MAX_TRANSIENT_STEP_OUTPUT = 20_000


@dataclass(frozen=True, slots=True)
class _StepRecord:
    outcome: StepOutcome
    started_at: datetime
    finished_at: datetime
    before: GitObservation
    after: GitObservation
    attempts: tuple[PlanStepAttemptEvidence, ...]
    outputs: dict[str, str]
    output_evidence: tuple[PublishedPlanOutputEvidence, ...]
    evidence_repository: Path | None


RetryExecutor = Callable[[ResolvedPlanStep, GitObservation, int], RetryExecutionResult]


class FleetPlanStepSequence:
    """Own ordered step state after direct/strict physical execution is selected."""

    def __init__(
        self,
        repository: Path,
        runtime: StepRuntime,
        output_observer: StepOutputObserver | None = None,
    ) -> None:
        self._repository = repository
        self._runtime = runtime
        self._output_observer = output_observer
        self._results: list[PlanStepExecution] = []
        self._stdout: list[str] = []
        self._stderr: list[str] = []
        self._published: dict[str, dict[str, str]] = {}
        self._halt_normal = False
        self._halt_all = False
        self._halt_all_reason = "Prior strict attempt teardown failed; execution stopped"

    @property
    def results(self) -> tuple[PlanStepExecution, ...]:
        return tuple(self._results)

    @property
    def stdout(self) -> str:
        return "\n".join(self._stdout)

    @property
    def stderr(self) -> str:
        return "\n".join(self._stderr)

    def run_direct(self, step: ResolvedPlanStep) -> None:
        source = self._runtime.project_workspaces.get(step.step_id, self._repository)
        try:
            record = self._run(
                step,
                source=source,
                imported_reason=None,
                execute_retries=lambda bound, before, step_order: execute_direct_with_retries(
                    source,
                    bound,
                    step,
                    self._runtime,
                    before,
                    self._output_observer,
                    step_order=step_order,
                ),
            )
        except OperationCancelledError:
            self._record_cancelled_lifecycle(step)
            raise
        artifacts = self._direct_artifacts(step, record)
        self._record(step, record, host_environment=None, artifacts=artifacts)
        self._record_terminal_lifecycle(step)

    def run_strict(self, prepared_run: PreparedPlanRun, step: ResolvedPlanStep) -> None:
        imported_reason = prepared_run.template_import_skip_reason(step.step_id)
        halted_before_source = self._halt_all or (self._halt_normal and not is_finalizer(step))
        try:
            source = (
                prepared_run.base_workspace
                if imported_reason or halted_before_source
                else prepared_run.source_for(step)
            )
            try:
                record = self._run(
                    step,
                    source=source,
                    imported_reason=imported_reason,
                    execute_retries=lambda bound, before, step_order: execute_strict_with_retries(
                        self._repository,
                        bound,
                        step,
                        prepared_run,
                        self._runtime,
                        before,
                        self._output_observer,
                        step_order=step_order,
                    ),
                )
            except OperationCancelledError:
                self._record_cancelled_lifecycle(step)
                raise
            try:
                artifacts = self._strict_artifacts(step, record, prepared_run)
            except FleetPlanInvalid as exc:
                prepared_run.discard_publications(step)
                record = replace(
                    record,
                    outcome=output_contract_failure(
                        step, exc, record.outcome.argv, base=record.outcome
                    ),
                    outputs={},
                    output_evidence=(),
                )
                artifacts = ()
            self._record(
                step,
                record,
                host_environment=prepared_run.host_environment,
                artifacts=artifacts,
            )
        finally:
            teardown = prepared_run.release_step(step)
        self._results[-1] = self._results[-1].model_copy(update={"teardown": teardown})
        if isinstance(teardown, FailedPlanStepTeardownEvidence):
            self._halt_all = True
            self._halt_all_reason = "Prior strict attempt teardown failed; execution stopped"
        self._record_output_lifecycle(step, record)
        self._record_terminal_lifecycle(step)

    def _record_output_lifecycle(self, step: ResolvedPlanStep, record: _StepRecord) -> None:
        observer = self._runtime.lifecycle_observer
        if observer is None or record.outcome.physical_execution_id is None or not record.attempts:
            return
        attempt_number = len(record.attempts)
        physical_execution_id = record.outcome.physical_execution_id
        if record.output_evidence:
            observer(
                step.step_id,
                len(self._results),
                "OUTPUT_FINALIZED",
                output_count=len(record.output_evidence),
                attempt_number=attempt_number,
                physical_execution_id=physical_execution_id,
            )
        elif record.outcome.returncode != 0:
            observer(
                step.step_id,
                len(self._results),
                "OUTPUT_ABANDONED",
                attempt_number=attempt_number,
                physical_execution_id=physical_execution_id,
            )

    def _record_cancelled_lifecycle(self, step: ResolvedPlanStep) -> None:
        observer = self._runtime.lifecycle_observer
        if observer is not None:
            observer(step.step_id, len(self._results) + 1, "STEP_CANCELLED")

    def _record_terminal_lifecycle(self, step: ResolvedPlanStep) -> None:
        observer = self._runtime.lifecycle_observer
        if observer is None:
            return
        execution = self._results[-1]
        step_order = len(self._results)
        if isinstance(execution, CompletedPlanStepExecution):
            observer(step.step_id, step_order, "STEP_SUCCEEDED", returncode=execution.returncode)
        elif isinstance(execution, FailedPlanStepExecution):
            observer(step.step_id, step_order, "STEP_FAILED", returncode=execution.returncode)
        else:
            observer(step.step_id, step_order, "STEP_SKIPPED")

    def _run(
        self,
        step: ResolvedPlanStep,
        *,
        source: Path,
        imported_reason: str | None,
        execute_retries: RetryExecutor,
    ) -> _StepRecord:
        started_at = utc_now()
        observer = self._runtime.lifecycle_observer
        step_order = len(self._results) + 1
        if observer is not None:
            observer(step.step_id, step_order, "STEP_REACHED")
        before = self._runtime.git.observe(source, self._runtime.cancel_requested)
        halted = self._halted(step, before)
        if halted is not None:
            outcome, after = halted
            return _StepRecord(outcome, started_at, utc_now(), before, after, (), {}, (), None)

        skip_reason = imported_reason or condition_skip_reason(source, step, tuple(self._results))
        if skip_reason is not None:
            return _StepRecord(
                StepOutcome(argv=(), returncode=0, skipped=True, stdout=skip_reason),
                started_at,
                utc_now(),
                before,
                before,
                (),
                {},
                (),
                None,
            )

        try:
            bound_step = bind_step_outputs(step, self._published)
        except FleetPlanInvalid as exc:
            return _StepRecord(
                output_contract_failure(step, exc, ()),
                started_at,
                utc_now(),
                before,
                before,
                (),
                {},
                (),
                None,
            )

        if observer is not None:
            observer(step.step_id, step_order, "STEP_STARTED")
        retry = execute_retries(bound_step, before, step_order)
        return _StepRecord(
            outcome=retry.outcome,
            started_at=started_at,
            finished_at=utc_now(),
            before=before,
            after=retry.after,
            attempts=retry.attempts,
            outputs=retry.outputs,
            output_evidence=retry.output_evidence,
            evidence_repository=retry.evidence_repository,
        )

    def _halted(
        self, step: ResolvedPlanStep, before: GitObservation
    ) -> tuple[StepOutcome, GitObservation] | None:
        if self._halt_all:
            return (
                StepOutcome(
                    argv=(),
                    returncode=0,
                    skipped=True,
                    stdout=self._halt_all_reason,
                ),
                before,
            )
        if self._halt_normal and not is_finalizer(step):
            return skipped_after_failure(step), before
        return None

    def _direct_artifacts(
        self, step: ResolvedPlanStep, record: _StepRecord
    ) -> tuple[PlanArtifactEvidence, ...]:
        if (
            record.evidence_repository is None
            or record.outcome.skipped
            or process_boundary_settled(record.outcome) is not True
        ):
            return ()
        return collect_direct_artifacts(record.evidence_repository, step.artifacts)

    def _strict_artifacts(
        self,
        step: ResolvedPlanStep,
        record: _StepRecord,
        prepared_run: PreparedPlanRun,
    ) -> tuple[PlanArtifactEvidence, ...]:
        if (
            record.evidence_repository is None
            or record.outcome.skipped
            or process_boundary_settled(record.outcome) is not True
        ):
            return ()
        return collect_strict_artifacts(record.evidence_repository, step.artifacts, prepared_run)

    def _record(
        self,
        step: ResolvedPlanStep,
        record: _StepRecord,
        *,
        host_environment: Mapping[str, str] | None,
        artifacts: tuple[PlanArtifactEvidence, ...],
    ) -> None:
        outcome = redact_host_secrets(step, record.outcome, host_environment)
        if outcome.stdout:
            self._stdout.append(outcome.stdout[:MAX_TRANSIENT_STEP_OUTPUT])
        if outcome.stderr:
            self._stderr.append(outcome.stderr[:MAX_TRANSIENT_STEP_OUTPUT])
        evidence = step_evidence(
            step=step,
            outcome=outcome,
            repository_state_before=record.before.fingerprint.state_id,
            repository_state_after=record.after.fingerprint.state_id,
            worktree_files_after=(
                record.after.changed_files if record.evidence_repository is not None else ()
            ),
            attempts=record.attempts,
            artifacts=artifacts,
            outputs=record.output_evidence,
            started_at=record.started_at,
            finished_at=record.finished_at,
        )
        self._results.append(evidence)
        if not outcome.skipped and process_boundary_settled(outcome) is not True:
            self._halt_all = True
            self._halt_all_reason = "Prior execution cleanup is unverified; execution stopped"
        if isinstance(evidence, CompletedPlanStepExecution):
            self._published[step.step_id] = record.outputs
        elif (
            isinstance(evidence, FailedPlanStepExecution)
            and failure_policy(step) is StepFailurePolicy.CONTINUE
            and record.outputs
        ):
            # Only the existing direct publication owner can supply validated
            # failed-command diagnostics. Publishing data never changes FAILURE.
            self._published[step.step_id] = record.outputs
        if (
            isinstance(evidence, FailedPlanStepExecution)
            and failure_policy(step) is StepFailurePolicy.STOP
            and not is_finalizer(step)
        ):
            self._halt_normal = True
