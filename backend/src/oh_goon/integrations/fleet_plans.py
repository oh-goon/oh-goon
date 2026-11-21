from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from oh_goon.core.canonical_result_semantics import CanonicalResultSemantics
from oh_goon.core.errors import CapabilityUnavailable, OperationDeadlineExpired, StateConflict
from oh_goon.core.models.candidate_execution_receipt import CandidateExecutionReceipt
from oh_goon.core.models.diagnostic import DiagnosticEvent
from oh_goon.core.models.explanation import ReasonCode
from oh_goon.core.models.fleet_plan_evidence import FailedPlanStepExecution, FleetPlanResult
from oh_goon.core.models.fleet_plan_execution_policy import ExecutionPurpose
from oh_goon.core.models.operation import (
    CompletedAttentionOperationOutcome,
    FailedOperationOutcome,
    FleetPlanOperationEvidence,
    OperationExecution,
    OperationReport,
    SuccessfulOperationOutcome,
)
from oh_goon.core.models.provenance import OperationProvenance, RepositoryStateFingerprint
from oh_goon.core.models.storage_maintenance import FleetContentMaintenanceReceipt
from oh_goon.core.models.target import FleetPlanTarget, OperationTarget
from oh_goon.core.path_authority import StrictHostPathAuthority
from oh_goon.core.ports.candidate_execution import (
    CandidateExecutionContext,
    CandidateExecutionFactory,
)
from oh_goon.core.ports.capability_runner import (
    CapabilityExecutionRequest,
    PreparedRunnerExecution,
)
from oh_goon.core.ports.diagnostics import DiagnosticEmitter
from oh_goon.core.ports.step_output import StepOutputObserver
from oh_goon.core.time import utc_now
from oh_goon.core.vocabulary import EffectBoundaryKind, OperationAction, Verdict
from oh_goon.core.vocabulary.result_semantics import CanonicalResultStatus
from oh_goon.engine.command import CommandTerminal
from oh_goon.engine.fleet_extensions import FleetExtensionStore
from oh_goon.engine.fleet_tools import FleetToolStore
from oh_goon.engine.git import GitService
from oh_goon.integrations.fleet_content_store import FleetContentStore
from oh_goon.integrations.fleet_plan_preview import require_repository_plan_admission_current
from oh_goon.integrations.fleet_plan_process_isolation import ProcessIsolationBackend
from oh_goon.integrations.fleet_plan_run import PreparedPlanRun
from oh_goon.integrations.fleet_plan_run_execution import FleetPlanRunExecutor, PlanExecutionOutcome
from oh_goon.integrations.fleet_plan_step_execution import StepLifecycleObserver

StepOutputObserverFactory = Callable[[str, str], StepOutputObserver]
StepLifecycleObserverFactory = Callable[[str, str, str], StepLifecycleObserver]


@dataclass(frozen=True, slots=True)
class PreparedStrictFleetPlanExecution:
    """Owned strict Fleet execution resources frozen before EFFECT_STARTED."""

    runner: FleetPlanRunner
    prepared_run: PreparedPlanRun

    @property
    def execution_input_id(self) -> str:
        return self.prepared_run.execution_input_evidence().input_id

    def execute(self, request: CapabilityExecutionRequest) -> OperationExecution:
        return self.runner._run_strict(self.prepared_run, request)

    @property
    def maintenance_receipt(self) -> FleetContentMaintenanceReceipt | None:
        return self.prepared_run.maintenance_receipt

    def close(self) -> None:
        self.prepared_run.close()


@dataclass(frozen=True, slots=True)
class PreparedCandidateFleetPlanExecution:
    """The original Run's one current candidate materialization, before effects."""

    runner: FleetPlanRunner
    candidate: CandidateExecutionContext
    fleet_run_id: str

    @property
    def execution_input_id(self) -> str:
        return self.candidate.materialization.materialization_id

    def execute(self, request: CapabilityExecutionRequest) -> OperationExecution:
        return self.runner._run_candidate(self.candidate, request)

    def close(self) -> None:
        try:
            self.candidate.close()
        except Exception as exc:
            self.runner._candidate_cleanup_failure(exc, self.fleet_run_id)


class FleetPlanRunner:
    """Bind then execute one resolved Fleet plan from its durable admitted request."""

    def __init__(
        self,
        git: GitService,
        extensions: FleetExtensionStore | None = None,
        tools: FleetToolStore | None = None,
        *,
        process_isolation_backends: tuple[ProcessIsolationBackend, ...],
        path_authority: StrictHostPathAuthority | None,
        content_store: FleetContentStore,
        run_parent: Path,
        output_observer_factory: StepOutputObserverFactory | None = None,
        lifecycle_observer_factory: StepLifecycleObserverFactory | None = None,
        diagnostics: DiagnosticEmitter | None = None,
        terminal: CommandTerminal | None = None,
        standard_process_isolation: ProcessIsolationBackend | None = None,
        workspace_pin: Callable[[Path], AbstractContextManager[Callable[[], None]]] | None = None,
        candidate_execution_factory: CandidateExecutionFactory | None = None,
    ) -> None:
        self._git = git
        self._content_store = content_store
        self._output_observer_factory = output_observer_factory
        self._lifecycle_observer_factory = lifecycle_observer_factory
        self._candidate_execution_factory = candidate_execution_factory
        self._diagnostics = diagnostics
        self._steps = FleetPlanRunExecutor(
            git,
            extensions,
            tools,
            process_isolation_backends=process_isolation_backends,
            path_authority=path_authority,
            content_store=content_store,
            run_parent=run_parent,
            diagnostics=diagnostics,
            terminal=terminal,
            standard_process_isolation=standard_process_isolation,
            workspace_pin=workspace_pin,
        )

    def effect_boundary(self, target: OperationTarget | None) -> EffectBoundaryKind:
        if not isinstance(target, FleetPlanTarget):
            raise CapabilityUnavailable(
                "Fleet plan capability requires a resolved Fleet plan target"
            )
        return self._effect_boundary(target)

    @staticmethod
    def _effect_boundary(target: FleetPlanTarget) -> EffectBoundaryKind:
        plan = target.plan
        if plan.execution.purpose is not ExecutionPurpose.CICD:
            return EffectBoundaryKind.EXTERNAL
        escapes = any(step.capabilities.egress.mode != "none" for step in plan.steps)
        return EffectBoundaryKind.EXTERNAL if escapes else EffectBoundaryKind.CONTAINED

    def prepare(self, request: CapabilityExecutionRequest) -> PreparedRunnerExecution:
        attempt = request.attempt
        target = attempt.target
        if not isinstance(target, FleetPlanTarget):
            raise CapabilityUnavailable(
                "Fleet plan capability requires a resolved Fleet plan target"
            )
        if attempt.action is not OperationAction.EXECUTE_PLAN:
            raise CapabilityUnavailable("Fleet plan runner requires execute-plan capability")

        if attempt.fleet_run_id is not None:
            if request.repository_admission is None:
                raise StateConflict(
                    "Fleet plan execution is missing repository/Plan admission evidence"
                )
            require_repository_plan_admission_current(
                attempt.repository,
                target.plan,
                request.repository_admission,
            )

        if target.plan.execution.purpose is not ExecutionPurpose.CICD:
            if (
                target.agent_repair is not None
                and utc_now() >= target.agent_repair.limits.deadline_at
            ):
                raise OperationDeadlineExpired("Agent repair admission deadline expired")
            if target.agent_deliveries:
                # Admission already selected this typed Step; verify the exact
                # retained packet is available before EFFECT_STARTED.
                delivery_for = self._agent_delivery_for(target, attempt.repository.repository_id)
                for delivery in target.agent_deliveries:
                    delivery_for(delivery.step_id, delivery.handoff_identity)
            if target.candidate_workspace is not None:
                if (
                    self._candidate_execution_factory is None
                    or request.require_execution_current is None
                    or request.require_settlement_current is None
                    or request.repository_admission is None
                    or target.candidate_workspace.fleet_run_id != attempt.fleet_run_id
                    or target.candidate_workspace.workspace.repository_id
                    != attempt.repository.repository_id
                    or target.candidate_workspace.source
                    != request.repository_admission.source_authority
                ):
                    raise StateConflict("Candidate execution lacks its original claimed authority")
                candidate = self._candidate_execution_factory(target.candidate_workspace, request)
                try:
                    candidate.require_current()
                    self._steps.prepare_direct_run(
                        attempt.repository,
                        target.plan,
                        cancel_requested=request.cancel_requested,
                        execution_workspace=candidate.path,
                        workspace_materialization=candidate.materialization,
                        workspace_directory_fd=candidate.workspace_directory_fd,
                        require_candidate_current=candidate.require_current,
                    )
                    return PreparedCandidateFleetPlanExecution(
                        self, candidate, target.candidate_workspace.fleet_run_id
                    )
                except BaseException:
                    try:
                        candidate.close()
                    except Exception as exc:
                        self._candidate_cleanup_failure(
                            exc, target.candidate_workspace.fleet_run_id
                        )
                    raise
            if request.isolation_backend is not None:
                raise StateConflict(
                    "Non-CI/CD Fleet plans cannot consume process-isolation admission evidence"
                )
            self._steps.prepare_direct_run(
                attempt.repository,
                target.plan,
                cancel_requested=request.cancel_requested,
            )
            return self

        if request.isolation_backend is None:
            raise StateConflict(
                "Strict Fleet CI/CD requires process-isolation authority admitted before RUN_ADMITTED"
            )
        assert request.repository_admission is not None
        prepared_run = self._steps.prepare_strict_run(
            attempt.repository,
            target.plan,
            cancel_requested=request.cancel_requested,
            admitted_source_authority=request.repository_admission.source_authority,
            admitted_isolation_backend=request.isolation_backend,
            admitted_run=request.admitted_run,
            execution_target=target,
        )
        return PreparedStrictFleetPlanExecution(
            runner=self,
            prepared_run=prepared_run,
        )

    @property
    def execution_input_id(self) -> None:
        return None

    def close(self) -> None:
        return None

    def execute(self, request: CapabilityExecutionRequest) -> OperationExecution:
        attempt = request.attempt
        target = cast(FleetPlanTarget, attempt.target)
        execution = self._steps.execute_direct_run(
            attempt.repository.path,
            target.plan,
            cancel_requested=request.cancel_requested,
            termination_requested=request.termination_requested,
            output_observer=self._output_observer(request),
            fleet_run_id=attempt.fleet_run_id,
            repository_id=attempt.repository.repository_id,
            lifecycle_observer=self._lifecycle_observer(request),
            agent_delivery_for=self._agent_delivery_for(
                target,
                attempt.repository.repository_id,
            )
            if target.agent_deliveries
            else None,
            repair_context=target.agent_repair,
            execution_target=target,
            execution_limits=target.agent_repair.limits
            if target.agent_repair is not None
            else None,
        )
        after = self._steps.observe_repository(
            attempt.repository.path,
            target.plan,
            request.cancel_requested,
            termination_requested=request.termination_requested,
        ).fingerprint
        return self._operation_execution(request, execution, after=after)

    def _agent_delivery_for(
        self, target: FleetPlanTarget, repository_id: str
    ) -> Callable[[str, str], bytes]:
        """One content-store transport port, bound to original admitted Step identity."""
        import hashlib

        from oh_goon.core.errors import FleetPlanInvalid
        from oh_goon.core.models.agent_source import MAX_PREWORK_PACKET_BYTES

        references = {item.step_id: item for item in target.agent_deliveries}

        def deliver(step_id: str, handoff_identity: str) -> bytes:
            reference = references.get(step_id)
            if reference is None or reference.handoff_identity != handoff_identity:
                raise FleetPlanInvalid("Agent original admitted Step delivery identity mismatch")
            stored = self._content_store.get(
                reference.reference_id,
                owner_repository_id=repository_id,
                expected_kind="file",
            )
            with stored.path.open("rb") as stream:
                packet = stream.read(MAX_PREWORK_PACKET_BYTES + 1)
            if len(packet) > MAX_PREWORK_PACKET_BYTES:
                raise FleetPlanInvalid("retained Agent source exceeds its delivery budget")
            if hashlib.sha256(packet).hexdigest() != reference.delivery_sha256:
                raise FleetPlanInvalid("retained Agent source changed before Step execution")
            return packet

        return deliver

    def _candidate_cleanup_failure(self, error: Exception, fleet_run_id: str) -> None:
        """Report resource cleanup independently without replacing original Run truth."""
        event = DiagnosticEvent.from_exception(
            error,
            severity="WARNING",
            source="candidate_workspace",
            owner="FleetPlanRunner",
            operation="close_candidate",
            execution_affected=False,
            failure_class="resource_cleanup_failed",
            fleet_run_id=fleet_run_id,
        )
        if self._diagnostics is not None:
            self._diagnostics.emit(event)
        else:
            logging.getLogger(__name__).warning(
                "Candidate workspace cleanup failed for Run %s (%s)",
                fleet_run_id,
                type(error).__name__,
            )

    def _run_candidate(
        self, candidate: CandidateExecutionContext, request: CapabilityExecutionRequest
    ) -> OperationExecution:
        """Execute original Steps on a verified physical candidate under the same Run."""
        attempt = request.attempt
        target = cast(FleetPlanTarget, attempt.target)
        if target.candidate_workspace is None:
            raise StateConflict("Candidate execution lost its original target binding")
        candidate.mark_writer_started()
        execution = self._steps.execute_direct_run(
            candidate.path,
            target.plan,
            cancel_requested=request.cancel_requested,
            termination_requested=request.termination_requested,
            output_observer=self._output_observer(request),
            fleet_run_id=attempt.fleet_run_id,
            repository_id=attempt.repository.repository_id,
            lifecycle_observer=self._lifecycle_observer(request),
            agent_delivery_for=self._agent_delivery_for(target, attempt.repository.repository_id),
            execution_target=target,
            workspace_materialization=candidate.materialization,
            workspace_directory_fd=candidate.workspace_directory_fd,
            require_candidate_current=candidate.require_current,
        )
        result = FleetPlanResult(plan_id=target.plan.plan_id, steps=execution.steps)
        receipt = candidate.settle(result)
        after = candidate.observe_subject()
        return self._operation_execution(request, execution, after=after, candidate_receipt=receipt)

    def _run_strict(
        self, prepared_run: PreparedPlanRun, request: CapabilityExecutionRequest
    ) -> OperationExecution:
        attempt = request.attempt
        target = cast(FleetPlanTarget, attempt.target)
        execution = self._steps.execute_strict_run(
            attempt.repository.path,
            target.plan,
            prepared_run,
            cancel_requested=request.cancel_requested,
            termination_requested=request.termination_requested,
            output_observer=self._output_observer(request),
            fleet_run_id=attempt.fleet_run_id,
            repository_id=attempt.repository.repository_id,
            lifecycle_observer=self._lifecycle_observer(request),
            local_lace_intent_for=request.local_lace_intent_for,
        )
        after = self._git.fingerprint(attempt.repository.path, request.cancel_requested)
        if after.state_id != attempt.baseline.state_id:
            raise StateConflict(
                "Canonical repository changed while an isolated Fleet CI/CD plan was executing"
            )
        return self._operation_execution(request, execution, after=after)

    def _lifecycle_observer(
        self, request: CapabilityExecutionRequest
    ) -> StepLifecycleObserver | None:
        factory = self._lifecycle_observer_factory
        fleet_run_id = request.attempt.fleet_run_id
        if factory is None or fleet_run_id is None:
            return None
        return factory(
            fleet_run_id, request.attempt.repository.repository_id, request.attempt.operation_id
        )

    def _output_observer(self, request: CapabilityExecutionRequest) -> StepOutputObserver | None:
        factory = self._output_observer_factory
        fleet_run_id = request.attempt.fleet_run_id
        if factory is None or fleet_run_id is None:
            return None
        return factory(fleet_run_id, request.attempt.repository.repository_id)

    def _operation_execution(
        self,
        request: CapabilityExecutionRequest,
        execution: PlanExecutionOutcome,
        *,
        after: RepositoryStateFingerprint,
        candidate_receipt: CandidateExecutionReceipt | None = None,
    ) -> OperationExecution:
        attempt = request.attempt
        target = cast(FleetPlanTarget, attempt.target)
        baseline = attempt.baseline
        steps = execution.steps
        result = FleetPlanResult(
            plan_id=target.plan.plan_id,
            steps=steps,
            execution_inputs=execution.execution_inputs,
        )
        canonical_result = CanonicalResultSemantics().interpret_plan(target.plan, result)
        changed = (
            candidate_receipt.checkpoint.source_sha256
            != target.candidate_workspace.checkpoint.source_sha256
            if candidate_receipt is not None
            and candidate_receipt.checkpoint is not None
            and target.candidate_workspace is not None
            else baseline.state_id != after.state_id
        )
        returncode = next(
            (step.returncode for step in steps if isinstance(step, FailedPlanStepExecution)),
            0,
        )
        report = OperationReport(
            capability=attempt.capability,
            target=target,
            provenance=OperationProvenance(
                operation_id=attempt.operation_id,
                fleet_run_id=attempt.fleet_run_id,
                started_at=baseline.captured_at,
                finished_at=utc_now(),
                before=baseline,
                after=after,
            ),
            repository=attempt.repository,
            outcome=(
                SuccessfulOperationOutcome(
                    state="successful",
                    verdict=Verdict.UPDATED if changed else Verdict.READY,
                    reason_code=ReasonCode.READY,
                )
                if canonical_result.status is CanonicalResultStatus.SUCCESS
                else CompletedAttentionOperationOutcome(
                    state="completed_attention",
                    returncode=returncode or 1,
                    reason_code=ReasonCode.OPERATION_FAILED,
                )
                if canonical_result.status is CanonicalResultStatus.ATTENTION_REQUIRED
                else FailedOperationOutcome(
                    state="failed",
                    returncode=returncode or 1,
                    reason_code=ReasonCode.OPERATION_FAILED,
                )
            ),
            evidence=FleetPlanOperationEvidence(
                kind="FLEET_PLAN",
                plan=result,
                canonical_result=canonical_result,
                candidate_receipt=candidate_receipt,
            ),
        )
        return OperationExecution(stdout=execution.stdout, stderr=execution.stderr, report=report)
