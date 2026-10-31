from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from oh_goon.services.fleet_local_lace_takeover import OriginalLocalLaceTakeoverCoordinator

from collections.abc import Callable, Iterator
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
from typing import Any, Literal

from pydantic import ValidationError

from oh_goon.core.capability_model import ResolvedCapabilityIntent
from oh_goon.core.capability_registry import (
    resolve_bound_capability_intent,
    resolve_capability_intent,
)
from oh_goon.core.errors import (
    ExecutorReadmissionNotAllowed,
    FleetContentIntegrityError,
    FleetContentObjectUnavailable,
    FleetContentReferenceUnavailable,
    FleetPlanInvalid,
    FleetPlanSourceStale,
    InvalidRunTransition,
    JournalConflict,
    StateConflict,
)
from oh_goon.core.file_digest import sha256_file_with_size
from oh_goon.core.game_tape_comparison import compare_game_tapes
from oh_goon.core.game_tape_failure_history import project_game_tape_failure_history
from oh_goon.core.game_tape_performance import project_game_tape_performance
from oh_goon.core.models.agent_repair import (
    AgentCampaignDecision,
    AgentCampaignIntent,
    AgentCampaignReadback,
    AgentCampaignRunEvidence,
    AgentRepairResult,
)
from oh_goon.core.models.capability import CapabilitySnapshot
from oh_goon.core.models.diagnostic import DiagnosticEvent
from oh_goon.core.models.execution_observability import ExecutionTimeBucket, FleetExecutionTimeline
from oh_goon.core.models.fleet_failures import (
    FleetFailureGroupPage,
    FleetFailureInventory,
    FleetFailureRepositoryPage,
)
from oh_goon.core.models.fleet_plan_evidence import PlanArtifactEvidence, PresentPlanArtifact
from oh_goon.core.models.fleet_plan_execution_policy import CicdExecutionPolicy
from oh_goon.core.models.fleet_plan_step_primitives import PlanArtifactDeclaration
from oh_goon.core.models.game_tape import (
    ArtifactAvailabilityReason,
    ArtifactAvailabilityStatus,
    ArtifactObservationCleanup,
    GameTape,
    GameTapeArtifactAvailability,
    GameTapeArtifactObservation,
    GameTapeArtifactObservationBudget,
    GameTapeRepositoryExecution,
    GameTapeRunDiff,
    GameTapeStepExecution,
)
from oh_goon.core.models.game_tape_failure_history import GameTapeFailureHistory
from oh_goon.core.models.game_tape_live_output import GameTapeLiveOutputBatch
from oh_goon.core.models.game_tape_performance import GameTapePerformanceReport
from oh_goon.core.models.run import (
    FleetRunDetail,
    FleetRunFailure,
    FleetRunHistoryPage,
    FleetRunOverview,
    FleetRunState,
)
from oh_goon.core.models.run_admission import (
    DirectFleetExecutionAdmission,
    ExplicitRepositoryScope,
    FleetRepositoryScope,
    InvalidRepositoryScopeRequest,
    requested_fleet_repository_scope,
)
from oh_goon.core.models.run_events import FleetRunEventBatch
from oh_goon.core.models.run_resources import RunResourceEnvelopeEvidence
from oh_goon.core.models.scheduling import SchedulingPriority
from oh_goon.core.models.target import AgentStepDeliveryReference, FleetPlanTarget, OperationTarget
from oh_goon.core.path_authority import RepositoryScopedPath
from oh_goon.core.ports.diagnostics import DiagnosticEmitter
from oh_goon.core.product_identity import new_fleet_run_id, repository_subject_instance_id
from oh_goon.core.projections.agent_campaign import project_agent_campaign
from oh_goon.core.run_identity import (
    require_fleet_run_detail_binding,
    require_fleet_run_scheduling_binding,
    with_retained_agent_deliveries,
    with_retained_project_deliveries,
)
from oh_goon.core.time import utc_now
from oh_goon.core.vocabulary import (
    FleetRunFailureStage,
    FleetRunOutcome,
    FleetRunSource,
    FleetRunStatus,
    FleetRunTerminalIntent,
    GameTapeStatus,
    OperationAction,
)
from oh_goon.engine.errors import OperationCancelledError
from oh_goon.integrations.content_snapshot import SnapshotIdentity
from oh_goon.integrations.exact_file_write import (
    ExactPublicationDurabilityError,
    publish_prepared_file,
)
from oh_goon.integrations.fleet_content_store import FleetContentStore
from oh_goon.runtime.execution import ExecutionTicket, RunExecutionSupervisor
from oh_goon.runtime.game_tape_live_output import GameTapeLiveOutputStore
from oh_goon.runtime.journal import Journal, RunResourceObservationContext
from oh_goon.runtime.schedule_executions import ScheduleExecutionStore
from oh_goon.services.fleet_admission import FleetAdmissionPlanner
from oh_goon.services.fleet_caller_file_freshness import require_target_caller_file_fresh
from oh_goon.services.fleet_rerun_scope import rerun_repository_scope
from oh_goon.services.fleet_run_execution import FleetRunExecution
from oh_goon.services.fleet_run_lifecycle import FleetRunLifecycle
from oh_goon.services.game_tape_live_output import read_game_tape_live_output


class _RecoveredRunSourceConflict(Exception):
    """Unwind only source custody created by a losing admission attempt."""

    def __init__(self, state: FleetRunState) -> None:
        super().__init__("Original Run retained different source delivery references")
        self.state = state


@dataclass(frozen=True, slots=True)
class FleetArtifactExport:
    """An explicitly promoted artifact verified against one recorded Run's evidence."""

    fleet_run_id: str
    repository_id: str
    step_id: str
    artifact_path: str
    destination: Path
    sha256: str
    byte_count: int
    reference_id: str
    cleanup_status: Literal["verified", "failed"] = "verified"
    cleanup_failure: str | None = None


class _ArtifactReadRefused(FleetPlanInvalid):
    def __init__(self, message: str, reason: ArtifactAvailabilityReason) -> None:
        super().__init__(message)
        self.reason = reason


def _artifact_declarations(
    detail: FleetRunDetail,
) -> dict[tuple[str, str], tuple[PlanArtifactDeclaration, ...]]:
    if not isinstance(detail.run.target, FleetPlanTarget):
        return {}
    entries: dict[tuple[str, str], tuple[PlanArtifactDeclaration, ...]] = {}
    for step in detail.run.target.plan.steps:
        for declaration in step.artifacts:
            key = (step.step_id, declaration.path)
            entries[key] = (*entries.get(key, ()), declaration)
    return entries


def _cleanup_artifact_export(temporary: TemporaryDirectory[str]) -> str | None:
    try:
        temporary.cleanup()
    except BaseException as error:
        return f"{type(error).__name__}: {error}; private staging: {temporary.name}"
    return None


def _recorded_export_artifact(
    detail: FleetRunDetail,
    *,
    step_id: str,
    artifact_path: str,
    repository_id: str | None,
) -> tuple[str, PresentPlanArtifact]:
    if not isinstance(detail.run.target, FleetPlanTarget):
        raise _ArtifactReadRefused(
            "The recorded Run has no declared Plan artifacts", "DECLARATION_MISMATCH"
        )
    tape = detail.game_tape
    _require_artifact_read_run(detail, tape)
    matches = [
        (repository.repository.repository_id, step)
        for repository in tape.repositories
        if repository_id is None or repository.repository.repository_id == repository_id
        for step in repository.steps
        if step.step_id == step_id
    ]
    if not matches:
        raise FleetPlanInvalid("No recorded Step matches the selected Run and repository")
    if len(matches) != 1:
        raise FleetPlanInvalid(
            "The recorded Step is ambiguous across repositories; select --repository-id "
            "from 'oh-goon runs steps RUN_ID --json'"
        )
    owner, step = matches[0]
    return owner, _qualified_artifact(
        detail, tape, owner, step, artifact_path, _artifact_declarations(detail)
    )


def _qualified_artifact(
    detail: FleetRunDetail,
    tape: GameTape,
    owner: str,
    step: GameTapeStepExecution,
    artifact_path: str,
    declarations: dict[tuple[str, str], tuple[PlanArtifactDeclaration, ...]],
) -> PresentPlanArtifact:
    _require_artifact_read_run(detail, tape)
    if (
        step.status not in {GameTapeStatus.PASSED, GameTapeStatus.FAILED}
        or step.settlement.outcome not in {"SUCCESS", "FAILED", "TIMED_OUT", "OUTPUT_LIMITED"}
        or step.settlement.cleanup != "VERIFIED"
    ):
        raise _ArtifactReadRefused(
            "Artifact export requires a settled Step with verified process cleanup",
            "PRODUCER_UNSETTLED",
        )
    selected = declarations.get((step.step_id, artifact_path), ())
    if len(selected) != 1:
        raise _ArtifactReadRefused(
            "The selected artifact was not declared for content-store retention",
            "DECLARATION_MISMATCH",
        )
    if selected[0].retention != "content_store":
        raise _ArtifactReadRefused(
            "The selected artifact was not declared for content-store retention", "NOT_RETAINED"
        )
    evidence = [artifact for artifact in step.artifacts if artifact.path == artifact_path]
    if len(evidence) > 1:
        raise _ArtifactReadRefused(
            "The selected artifact has no recorded present file", "DECLARATION_MISMATCH"
        )
    if not evidence or not isinstance(evidence[0], PresentPlanArtifact):
        raise _ArtifactReadRefused(
            "The selected artifact has no recorded present file", "NOT_COLLECTED"
        )
    artifact = evidence[0]
    retained = artifact.retained_object
    if retained is None:
        raise _ArtifactReadRefused(
            "The selected artifact has no recorded retained content reference", "NOT_RETAINED"
        )
    if (
        retained.owner_repository_id != owner
        or retained.kind != "file"
        or retained.byte_count != artifact.byte_count
        or retained.entry_count != 1
    ):
        raise _ArtifactReadRefused(
            "The retained artifact reference does not match its Run evidence",
            "OWNER_MISMATCH" if retained.owner_repository_id != owner else "IDENTITY_MISMATCH",
        )
    return artifact


def _require_artifact_read_run(detail: FleetRunDetail, tape: GameTape) -> None:
    if not isinstance(detail.run.target, FleetPlanTarget):
        raise _ArtifactReadRefused(
            "The recorded Run has no declared Plan artifacts", "DECLARATION_MISMATCH"
        )
    if detail.run.status in {
        FleetRunStatus.CANCELLING,
        FleetRunStatus.CANCELLED,
    } or tape.status in {
        GameTapeStatus.CANCELLED,
        GameTapeStatus.INTERRUPTED,
    }:
        raise _ArtifactReadRefused(
            "Artifact export refuses a cancelling, cancelled or interrupted Run",
            "RUN_INTERRUPTED" if tape.status is GameTapeStatus.INTERRUPTED else "RUN_CANCELLED",
        )


def _verify_retained_artifact(
    store: FleetContentStore,
    artifact: PresentPlanArtifact,
    staged: RepositoryScopedPath,
    *,
    cancel_requested: Callable[[], bool] | None = None,
) -> None:
    retained = artifact.retained_object
    assert retained is not None
    observed = store.materialize_file(
        retained.reference_id,
        staged,
        expected_identity=SnapshotIdentity(
            retained.sha256, retained.byte_count, retained.entry_count
        ),
        cancel_requested=cancel_requested,
    )
    if (
        observed.sha256 != retained.sha256
        or observed.byte_count != retained.byte_count
        or observed.entry_count != retained.entry_count
    ):
        raise FleetContentIntegrityError(
            "Retained artifact bytes differ from the recorded Run evidence"
        )

    def check_cancel() -> None:
        if cancel_requested is not None and cancel_requested():
            raise OperationCancelledError("Cancelled while verifying retained artifact bytes")

    raw_sha256, byte_count = sha256_file_with_size(staged.path, check_cancel=check_cancel)
    if raw_sha256 != artifact.sha256 or byte_count != artifact.byte_count:
        raise FleetContentIntegrityError(
            "Exported artifact bytes differ from their recorded SHA-256"
        )


def _artifact_observation_result(
    repository: GameTapeRepositoryExecution,
    step: GameTapeStepExecution,
    record: PlanArtifactEvidence,
    status: ArtifactAvailabilityStatus,
    reason: ArtifactAvailabilityReason,
    staging_cleanup: ArtifactObservationCleanup = "NOT_REQUIRED",
) -> GameTapeArtifactAvailability:
    present = record if isinstance(record, PresentPlanArtifact) else None
    return GameTapeArtifactAvailability(
        repository_execution_id=repository.repository_execution_id,
        repository_id=repository.repository.repository_id,
        step_execution_id=step.step_execution_id,
        step_id=step.step_id,
        path=record.path,
        recorded_status=record.status,
        recorded_sha256=present.sha256 if present is not None else None,
        recorded_byte_count=present.byte_count if present is not None else None,
        reference_id=(
            present.retained_object.reference_id
            if present is not None and present.retained_object is not None
            else None
        ),
        status=status,
        reason=reason,
        checked_at=utc_now(),
        staging_cleanup=staging_cleanup,
        cleanup_failure="STAGING_CLEANUP_FAILED" if staging_cleanup == "FAILED" else None,
    )


def _artifact_observation_budget(
    observe: bool,
    artifact_limit: int | None,
    byte_budget: int | None,
    time_budget: float | None,
) -> GameTapeArtifactObservationBudget | None:
    if not observe:
        if any(value is not None for value in (artifact_limit, byte_budget, time_budget)):
            raise FleetPlanInvalid("Artifact observation budgets require observe_artifacts")
        return None
    defaults = GameTapeArtifactObservationBudget()
    try:
        return GameTapeArtifactObservationBudget(
            artifact_limit=defaults.artifact_limit if artifact_limit is None else artifact_limit,
            byte_budget=defaults.byte_budget if byte_budget is None else byte_budget,
            time_budget_seconds=defaults.time_budget_seconds
            if time_budget is None
            else time_budget,
        )
    except ValidationError:
        raise FleetPlanInvalid(
            "Artifact observation budget is outside the supported bounds"
        ) from None


class FleetRunControlPlane:
    """Fleet-run lifecycle/read control plane; execution and durable truth stay delegated."""

    def __init__(
        self,
        admission: FleetAdmissionPlanner,
        journal: Journal,
        lifecycle: FleetRunLifecycle,
        run_execution: FleetRunExecution,
        supervisor: RunExecutionSupervisor,
        live_output: GameTapeLiveOutputStore,
        diagnostics: DiagnosticEmitter | None = None,
        schedule_executions: ScheduleExecutionStore | None = None,
        content_store: FleetContentStore | None = None,
    ) -> None:
        self._admission = admission
        self._journal = journal
        self._lifecycle = lifecycle
        self._supervisor = supervisor
        self._run_execution = run_execution
        self._live_output = live_output
        self._diagnostics = diagnostics
        self._schedule_executions = schedule_executions
        self._content_store = content_store

    def require_cli_plan_preparation(self, fleet_run_id: str | None) -> None:
        """Delegate CLI pre-resolution admission to the original Fleet lifecycle."""
        self.require_plan_ingress_preparation(fleet_run_id)

    def existing_owned_local_lace_ticket(self, run_id: str) -> ExecutionTicket:
        """Delegate to the real live Fleet lease owner; not a recovery admission."""
        return self._lifecycle.existing_owned_local_lace_ticket(run_id)

    def transfer_original_dead_fleet_recovery(
        self,
        *,
        run_id: str,
        predecessor_token: str,
        expected_epoch: int,
    ) -> ExecutionTicket:
        """Delegate explicit custody transfer to original lifecycle; no new ingress."""
        return self._lifecycle.transfer_original_dead_fleet_recovery(
            run_id=run_id,
            predecessor_token=predecessor_token,
            expected_epoch=expected_epoch,
        )

    def resume_local_lace_takeover(
        self,
        ticket: ExecutionTicket,
        coordinator: OriginalLocalLaceTakeoverCoordinator,
        *,
        intent_run_seq: int,
    ) -> tuple[int, int]:
        """Delegate explicit worker recovery; this does NOT mint a takeover ticket.

        It is deliberately not an HTTP/CLI ingress and not called by the
        independent startup effect reconciler. Only the original admitted
        Fleet worker's live ExecutionTicket may reach native observation.
        """
        return self._run_execution.resume_local_lace_takeover(
            ticket, coordinator, intent_run_seq=intent_run_seq
        )

    def require_plan_ingress_preparation(self, fleet_run_id: str | None) -> None:
        """Delegate CLI/HTTP identity membership to one original lifecycle owner."""
        self._lifecycle.require_plan_ingress_preparation(fleet_run_id)

    def capture_run_resource_observation_context(
        self, expected: RunResourceEnvelopeEvidence
    ) -> RunResourceObservationContext:
        """Transport the original terminal child cut after its runtime has quiesced."""
        return self._journal.capture_run_resource_observation_context(expected)

    def start(
        self,
        action: OperationAction,
        *,
        target: OperationTarget | None = None,
        repository_ids: set[str] | None = None,
        all_repositories: bool = False,
        expected_repository_instance_ids: dict[str, str] | None = None,
        fleet_run_id: str | None = None,
        requested_priority: SchedulingPriority = SchedulingPriority.NORMAL,
    ) -> FleetRunState:
        # A Plan-shaped target is never a generic Fleet operation, even if a
        # direct caller supplies an unrelated action and skips HTTP validation.
        # Reject before capability routing, repository selection, or planning.
        if action is OperationAction.EXECUTE_PLAN or isinstance(target, FleetPlanTarget):
            raise StateConflict(
                "Fleet Plan execution must enter through FleetPlanService so the Plan is "
                "resolved and frozen by the authoritative execution-entry boundary"
            )
        intent = resolve_capability_intent(action, target)
        return self._start_intent(
            intent,
            repository_scope=_requested_repository_scope(
                intent, repository_ids=repository_ids, all_repositories=all_repositories
            ),
            fleet_run_id=fleet_run_id,
            expected_repository_instance_ids=expected_repository_instance_ids,
            source=FleetRunSource.MANUAL,
            readmitted_from_run_id=None,
            rerun_from_run_id=None,
            requested_priority=requested_priority,
        )

    def start_prepared_plan(
        self,
        target: FleetPlanTarget,
        *,
        repository_ids: set[str] | None = None,
        all_repositories: bool = False,
        fleet_run_id: str | None = None,
        source: FleetRunSource = FleetRunSource.MANUAL,
        expected_repository_instance_ids: dict[str, str] | None = None,
        requested_priority: SchedulingPriority = SchedulingPriority.NORMAL,
        created_agent_deliveries: tuple[AgentStepDeliveryReference, ...] = (),
    ) -> FleetRunState:
        """Start only a Fleet Plan target already frozen by FleetPlanService."""
        if created_agent_deliveries and (
            repository_ids is None
            or len(repository_ids) != 1
            or any(item not in target.agent_deliveries for item in created_agent_deliveries)
        ):
            raise FleetPlanInvalid("New Agent delivery custody requires its exact target and Team")
        selected_run_id = fleet_run_id or new_fleet_run_id()
        failure: BaseException | None = None
        try:
            intent = resolve_capability_intent(OperationAction.EXECUTE_PLAN, target)
            return self._start_intent(
                intent,
                repository_scope=_requested_repository_scope(
                    intent, repository_ids=repository_ids, all_repositories=all_repositories
                ),
                fleet_run_id=selected_run_id,
                expected_repository_instance_ids=expected_repository_instance_ids,
                source=source,
                readmitted_from_run_id=None,
                rerun_from_run_id=None,
                requested_priority=requested_priority,
            )
        except BaseException as exc:
            failure = exc
            raise
        finally:
            if created_agent_deliveries:
                assert repository_ids is not None
                try:
                    self._lifecycle.release_unadmitted_agent_deliveries(
                        selected_run_id,
                        created_agent_deliveries,
                        owner_repository_id=next(iter(repository_ids)),
                    )
                except Exception as exc:
                    if failure is not None:
                        failure.add_note(f"Unadmitted Agent source cleanup failed: {exc}")
                    event = DiagnosticEvent.from_exception(
                        exc,
                        severity="WARNING",
                        source="agent_source_transport",
                        owner="FleetRunLifecycle",
                        operation="release_unadmitted_agent_deliveries",
                        execution_affected=False,
                        failure_class="resource_cleanup_failed",
                        fleet_run_id=selected_run_id,
                    )
                    if self._diagnostics is not None:
                        self._diagnostics.emit(event)
                    else:
                        from oh_goon.runtime.diagnostics import (
                            DiagnosticRouter,
                            StderrDiagnosticSink,
                        )

                        DiagnosticRouter((StderrDiagnosticSink(),)).emit(event)

    def repository_instance_ids_for_prepared_plan(
        self,
        target: FleetPlanTarget,
        *,
        repository_ids: set[str],
        expected_repository_instance_ids: dict[str, str] | None = None,
    ) -> dict[str, str]:
        intent = resolve_capability_intent(OperationAction.EXECUTE_PLAN, target)
        scope = _requested_repository_scope(
            intent, repository_ids=repository_ids, all_repositories=False
        )
        instances = self._admission.repository_instance_ids(intent, repository_scope=scope)
        if (
            expected_repository_instance_ids is not None
            and instances != expected_repository_instance_ids
        ):
            raise FleetPlanSourceStale(
                "Schedule repository selection changed during preparation; reload the current "
                "League before creating the Schedule"
            )
        return instances

    @contextmanager
    def project_source_transport_for_prepared_plan(
        self,
        target: FleetPlanTarget,
        *,
        repository_ids: set[str],
        expected_repository_instance_ids: dict[str, str] | None = None,
    ) -> Iterator[tuple[FleetPlanTarget, dict[str, str]]]:
        """Retain Schedule source with the original Fleet input-custody lifecycle."""
        intent = resolve_capability_intent(OperationAction.EXECUTE_PLAN, target)
        scope = _requested_repository_scope(
            intent, repository_ids=repository_ids, all_repositories=False
        )
        repositories = self._admission.repository_targets(intent, repository_scope=scope)
        instances = {
            repository.repository_id: repository_subject_instance_id(repository.path)
            for repository in repositories
        }
        if (
            expected_repository_instance_ids is not None
            and instances != expected_repository_instance_ids
        ):
            raise FleetPlanSourceStale(
                "Schedule repository selection changed during preparation; reload the current "
                "League before creating the Schedule"
            )
        with self._lifecycle.project_source_transport(intent, repositories) as bound:
            assert isinstance(bound.target, FleetPlanTarget)
            # Durable receivers need only the retained references, never private packets.
            frozen = FleetPlanTarget.model_validate(
                bound.target.model_dump(exclude_computed_fields=True)
            )
            yield frozen, instances

    def start_bound(
        self,
        capability: CapabilitySnapshot,
        *,
        target: OperationTarget | None = None,
        repository_scope: FleetRepositoryScope,
        fleet_run_id: str | None = None,
        source: FleetRunSource,
        expected_repository_instance_ids: dict[str, str] | None = None,
        schedule_run_id: str | None = None,
    ) -> FleetRunState:
        """Start queued durable intent only when current executable policy is unchanged."""
        intent = resolve_bound_capability_intent(capability, target)
        return self._start_intent(
            intent,
            repository_scope=repository_scope,
            fleet_run_id=fleet_run_id,
            expected_repository_instance_ids=expected_repository_instance_ids,
            source=source,
            readmitted_from_run_id=None,
            rerun_from_run_id=None,
            schedule_run_id=schedule_run_id,
        )

    def readmit(
        self,
        failed_fleet_run_id: str,
        *,
        new_fleet_run_id: str,
    ) -> FleetRunState:
        """Create a new strict CI/CD admission from a placement-lost run; never mutate it."""
        return _readmit_run(self, failed_fleet_run_id, new_fleet_run_id=new_fleet_run_id)

    def rerun(
        self,
        fleet_run_id: str,
        *,
        new_fleet_run_id: str,
    ) -> FleetRunState:
        """Freshly admit the exact durable authority of a terminal Run under a new identity."""
        return _rerun_run(self, fleet_run_id, new_fleet_run_id=new_fleet_run_id)

    def _start_intent(
        self,
        intent: ResolvedCapabilityIntent,
        *,
        repository_scope: FleetRepositoryScope,
        fleet_run_id: str | None,
        expected_repository_instance_ids: dict[str, str] | None = None,
        source: FleetRunSource,
        readmitted_from_run_id: str | None,
        rerun_from_run_id: str | None,
        requested_priority: SchedulingPriority = SchedulingPriority.NORMAL,
        schedule_run_id: str | None = None,
    ) -> FleetRunState:
        # Fail before repository planning or lease attempts when deployment
        # explicitly requires signed Fleet admission but has no trust binding.
        fleet_run_id = fleet_run_id or new_fleet_run_id()

        def require_scheduled_child() -> None:
            if source is not FleetRunSource.SCHEDULED:
                if schedule_run_id is not None:
                    raise StateConflict("Only scheduled Fleet admission may bind an occurrence")
                return
            store = self._schedule_executions
            if store is None or schedule_run_id is None or fleet_run_id is None:
                raise StateConflict(
                    "Scheduled Fleet admission requires durable occurrence authority"
                )
            if (
                not isinstance(repository_scope, ExplicitRepositoryScope)
                or len(repository_scope.repository_ids) != 1
            ):
                raise StateConflict("Scheduled Fleet admission requires exactly one repository")
            if not isinstance(intent.target, FleetPlanTarget):
                raise StateConflict(
                    "Scheduled Fleet admission requires frozen Fleet Plan authority"
                )
            store.require_pending_child_binding(
                schedule_run_id=schedule_run_id,
                repository_id=repository_scope.repository_ids[0],
                fleet_run_id=fleet_run_id,
                capability=intent.definition.durable_snapshot(),
                target=intent.target,
                repository_instance_ids=expected_repository_instance_ids,
            )

        require_target_caller_file_fresh(intent.target)
        # No prework issuer observation, idempotent journal lookup, or Plan
        # planning may occur on an unbound/forged scheduled child.
        require_scheduled_child()
        self._lifecycle.require_fleet_prework_available(
            intent.target,
            fleet_run_id,
            readmitted_from_run_id=readmitted_from_run_id,
            rerun_from_run_id=rerun_from_run_id,
        )
        with self._lifecycle.serialize_admission(f"run:{fleet_run_id}"):
            existing = _matching_existing_run(
                self._journal,
                fleet_run_id,
                intent,
                repository_scope=repository_scope,
                source=source,
                readmitted_from_run_id=readmitted_from_run_id,
                rerun_from_run_id=rerun_from_run_id,
                requested_priority=requested_priority,
            )
            if existing is not None:
                return existing
            plan = self._admission.plan(
                intent,
                repository_scope=repository_scope,
                expected_repository_instance_ids=expected_repository_instance_ids,
            )
            # The Plan's revision is the CAS authority for the original Fleet
            # admission transaction; re-read the occurrence after that cut to
            # prevent a cancelled/completed child slipping through planning.
            require_scheduled_child()
            require_target_caller_file_fresh(intent.target)
            transport = (
                self._lifecycle.project_source_transport(
                    intent, tuple(entry.repository for entry in plan.targets)
                )
                if isinstance(intent.target, FleetPlanTarget)
                and any(
                    getattr(step, "project_source", None) is not None
                    for step in intent.target.plan.steps
                )
                else nullcontext(intent)
            )
            original_intent = intent
            try:
                with transport as intent:
                    try:
                        ticket, state = self._lifecycle.admit(
                            fleet_run_id,
                            intent,
                            repository_scope,
                            [entry.repository for entry in plan.targets],
                            expected_durable_revision=plan.durable_revision,
                            repository_admissions=plan.repository_admissions,
                            source=source,
                            readmitted_from_run_id=readmitted_from_run_id,
                            rerun_from_run_id=rerun_from_run_id,
                            requested_priority=requested_priority,
                            schedule_run_id=schedule_run_id,
                        )
                    except JournalConflict:
                        existing = _matching_existing_run(
                            self._journal,
                            fleet_run_id,
                            original_intent,
                            repository_scope=repository_scope,
                            source=source,
                            readmitted_from_run_id=readmitted_from_run_id,
                            rerun_from_run_id=rerun_from_run_id,
                            requested_priority=requested_priority,
                        )
                        if existing is None:
                            raise
                        if (
                            existing.target is not None
                            and intent.target is not None
                            and existing.target.model_dump(exclude_computed_fields=True)
                            != intent.target.model_dump(exclude_computed_fields=True)
                        ):
                            raise _RecoveredRunSourceConflict(existing) from None
                        # A lost ACK for this exact target must retain its committed inputs.
                        return existing
            except _RecoveredRunSourceConflict as recovered:
                if getattr(recovered, "__notes__", ()):
                    raise FleetPlanInvalid(
                        "Unadmitted Python Goon source cleanup failed"
                    ) from recovered
                return recovered.state
            self._submit(fleet_run_id, ticket)
            return state

    def cancel(self, fleet_run_id: str) -> FleetRunState:
        return self._lifecycle.cancel(fleet_run_id)

    def get(
        self,
        fleet_run_id: str,
        *,
        observe_artifacts: bool = False,
        artifact_limit: int | None = None,
        artifact_byte_budget: int | None = None,
        artifact_time_budget_seconds: float | None = None,
    ) -> FleetRunDetail:
        budget = _artifact_observation_budget(
            observe_artifacts, artifact_limit, artifact_byte_budget, artifact_time_budget_seconds
        )
        detail = self._journal.read_view().required_run_detail(fleet_run_id)
        try:
            from oh_goon.integrations.hashmarks.runtime_contract import (
                loaded_hashmarks_producer_provenance,
            )

            provenance = loaded_hashmarks_producer_provenance()
        except ImportError:
            provenance = None
        if provenance is not None:
            detail = detail.model_copy(update={"producer_implementation_provenance": provenance})
        if budget is not None:
            detail = detail.model_copy(
                update={"artifact_observation": self._observe_artifact_availability(detail, budget)}
            )
        return detail

    def _observe_artifact_availability(
        self, detail: FleetRunDetail, budget: GameTapeArtifactObservationBudget
    ) -> GameTapeArtifactObservation:
        """Verify a bounded sample from one captured read cut without changing history."""
        started = utc_now()
        deadline = monotonic() + budget.time_budget_seconds
        tape = detail.game_tape
        declarations = _artifact_declarations(detail)
        candidates: list[
            tuple[GameTapeRepositoryExecution, GameTapeStepExecution, PlanArtifactEvidence]
        ] = []
        total = 0
        for repository in tape.repositories:
            for step in repository.steps:
                for record in step.artifacts:
                    total += 1
                    if len(candidates) < budget.artifact_limit:
                        candidates.append((repository, step, record))
        observations: list[GameTapeArtifactAvailability] = []
        scheduled = 0
        for repository, step, record in candidates:
            try:
                artifact = _qualified_artifact(
                    detail,
                    tape,
                    repository.repository.repository_id,
                    step,
                    record.path,
                    declarations,
                )
            except _ArtifactReadRefused as error:
                state: ArtifactAvailabilityStatus = (
                    "UNAVAILABLE"
                    if error.reason in {"NOT_COLLECTED", "NOT_RETAINED"}
                    else "REFUSED"
                )
                observations.append(
                    _artifact_observation_result(repository, step, record, state, error.reason)
                )
                continue
            if self._content_store is None:
                reason: ArtifactAvailabilityReason = "STORE_UNAVAILABLE"
            elif monotonic() >= deadline:
                reason = "TIME_BUDGET"
            elif artifact.byte_count > budget.byte_budget - scheduled:
                reason = "BYTE_BUDGET"
            else:
                scheduled += artifact.byte_count
                observations.append(
                    self._observe_retained_artifact(repository, step, artifact, deadline)
                )
                continue
            observations.append(
                _artifact_observation_result(repository, step, record, "UNOBSERVED", reason)
            )
        return GameTapeArtifactObservation(
            **budget.model_dump(),
            fleet_run_id=detail.run.fleet_run_id,
            journal_revision=detail.journal_revision,
            started_at=started,
            finished_at=utc_now(),
            total_artifacts=total,
            omitted_artifacts=total - len(observations),
            bytes_scheduled=scheduled,
            observations=tuple(observations),
        )

    def _observe_retained_artifact(
        self,
        repository: GameTapeRepositoryExecution,
        step: GameTapeStepExecution,
        artifact: PresentPlanArtifact,
        deadline: float,
    ) -> GameTapeArtifactAvailability:
        store = self._content_store
        assert store is not None
        temporary: TemporaryDirectory[str] | None = None
        state: ArtifactAvailabilityStatus = "UNOBSERVED"
        reason: ArtifactAvailabilityReason = "READ_FAILED"
        cleanup: ArtifactObservationCleanup = "NOT_REQUIRED"
        try:
            temporary = TemporaryDirectory(prefix=".oh-goon-artifact-observation-")
            staged = RepositoryScopedPath.create(
                owner_repository_id=repository.repository.repository_id,
                root=Path(temporary.name),
                path=Path(temporary.name) / "artifact",
                label="recorded artifact availability observation",
            )
            _verify_retained_artifact(
                store, artifact, staged, cancel_requested=lambda: monotonic() >= deadline
            )
            if monotonic() >= deadline:
                reason = "TIME_BUDGET"
            else:
                state, reason = "AVAILABLE", "VERIFIED"
        except FleetContentReferenceUnavailable:
            state, reason = "UNAVAILABLE", "REFERENCE_UNAVAILABLE"
        except FleetContentObjectUnavailable:
            state, reason = "UNAVAILABLE", "OBJECT_MISSING"
        except FleetContentIntegrityError:
            state, reason = "UNAVAILABLE", "IDENTITY_MISMATCH"
        except OperationCancelledError:
            reason = "TIME_BUDGET" if monotonic() >= deadline else "READ_FAILED"
        except (FleetPlanInvalid, OSError):
            # No exception-string classifier and no private store paths in the read model.
            reason = "READ_FAILED"
        finally:
            if temporary is not None:
                failure = _cleanup_artifact_export(temporary)
                cleanup = "VERIFIED" if failure is None else "FAILED"
        return _artifact_observation_result(repository, step, artifact, state, reason, cleanup)

    def export_artifact(
        self,
        fleet_run_id: str,
        *,
        step_id: str,
        artifact_path: str,
        destination: Path,
        repository_id: str | None = None,
    ) -> FleetArtifactExport:
        """Export only retained bytes bound to an exact recorded Step and declaration."""
        detail = self.get(fleet_run_id)
        owner, artifact = _recorded_export_artifact(
            detail, step_id=step_id, artifact_path=artifact_path, repository_id=repository_id
        )
        retained = artifact.retained_object
        assert retained is not None
        if self._content_store is None:
            raise FleetPlanInvalid("The retained artifact content store is unavailable")
        target = destination.expanduser().absolute()
        if ".." in target.parts or any(part.is_symlink() for part in (target, *target.parents)):
            raise FleetPlanInvalid(
                "Artifact export destination cannot use traversal or symlink aliases"
            )
        if target.exists():
            raise FleetPlanInvalid("Artifact export destination already exists; choose a new file")
        if not target.parent.is_dir():
            raise FleetPlanInvalid(
                "Artifact export destination requires an existing parent directory"
            )
        temporary = TemporaryDirectory(prefix=".oh-goon-export-", dir=target.parent)
        try:
            staged = Path(temporary.name) / "artifact"
            _verify_retained_artifact(
                self._content_store,
                artifact,
                RepositoryScopedPath.create(
                    owner_repository_id=owner,
                    root=Path(temporary.name),
                    path=staged,
                    label="recorded artifact export",
                ),
            )
            publish_prepared_file(staged, target)
        except BaseException as error:
            if isinstance(error, ExactPublicationDurabilityError):
                error.add_note(
                    f"Artifact destination was published at {target}; its durability is unverified."
                )
            cleanup_failure = _cleanup_artifact_export(temporary)
            if cleanup_failure is not None:
                error.add_note("Artifact export staging cleanup failed: " + cleanup_failure)
            raise
        cleanup_failure = _cleanup_artifact_export(temporary)
        return FleetArtifactExport(
            fleet_run_id=fleet_run_id,
            repository_id=owner,
            step_id=step_id,
            artifact_path=artifact.path,
            destination=target,
            sha256=artifact.sha256,
            byte_count=artifact.byte_count,
            reference_id=retained.reference_id,
            cleanup_status="verified" if cleanup_failure is None else "failed",
            cleanup_failure=cleanup_failure,
        )

    def begin_agent_campaign(self, intent: AgentCampaignIntent) -> AgentCampaignReadback:
        return self._journal.begin_agent_campaign(intent)

    def campaign_journal(self, campaign_id: str) -> AgentCampaignReadback:
        return self._journal.agent_campaign(campaign_id)

    def campaign_decision_inputs(
        self, campaign_id: str
    ) -> tuple[AgentCampaignReadback, tuple[AgentCampaignRunEvidence, ...], tuple[str, ...]]:
        return self._journal.agent_campaign_decision_inputs(campaign_id)

    def commit_agent_campaign_decision(
        self, decision: AgentCampaignDecision
    ) -> AgentCampaignReadback:
        return self._journal.commit_agent_campaign_decision(decision)

    def agent_campaign_result(self, campaign_id: str) -> AgentRepairResult:
        return self._journal.agent_campaign_result(campaign_id)

    def agent_campaign(self, campaign_or_run_id: str) -> dict[str, Any]:
        """Inspect campaign intent and linked Runs from one verified Journal cut."""
        view = self._journal.read_view()
        campaign = view.campaign(campaign_or_run_id)
        if campaign.intent is None:
            selected = view.required_run_detail(campaign_or_run_id)
            target = selected.run.target
            if not isinstance(target, FleetPlanTarget) or target.agent_repair is None:
                raise FleetPlanInvalid("Selected Run is not part of a repair campaign")
            campaign = view.campaign(target.agent_repair.repair_id)
        return project_agent_campaign(
            campaign.campaign_id,
            tuple(view.required_run_detail(phase.fleet_run_id) for phase in campaign.phases),
            journal_revision=view.revision,
            campaign=campaign,
        )

    def history(
        self,
        *,
        limit: int = 20,
        before_revision: int | None = None,
        outcome: FleetRunOutcome | None = None,
        status: FleetRunStatus | None = None,
        source: FleetRunSource | None = None,
        repository_id: str | None = None,
        from_at: datetime | None = None,
        to_at: datetime | None = None,
        plan_query: str | None = None,
        plan_id: str | None = None,
    ) -> FleetRunHistoryPage:
        return self._journal.read_view().run_history(
            limit=limit,
            before_revision=before_revision,
            outcome=outcome,
            status=status,
            source=source,
            repository_id=repository_id,
            from_at=from_at,
            to_at=to_at,
            plan_query=plan_query,
            plan_id=plan_id,
        )

    def overview(self) -> FleetRunOverview:
        return self._journal.read_view().run_overview()

    def execution_timeline(
        self,
        *,
        from_at: datetime,
        to_at: datetime,
        bucket: ExecutionTimeBucket,
        plan_query: str | None = None,
        repository_query: str | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> FleetExecutionTimeline:
        return self._journal.read_view().execution_timeline(
            from_at=from_at,
            to_at=to_at,
            bucket=bucket,
            plan_query=plan_query,
            repository_query=repository_query,
            offset=offset,
            limit=limit,
        )

    def failure_inventory(self) -> FleetFailureInventory:
        return self._journal.read_view().failure_inventory()

    def failure_groups(
        self, *, query: str | None = None, offset: int = 0, limit: int = 50
    ) -> FleetFailureGroupPage:
        return self._journal.read_view().failure_groups(query=query, offset=offset, limit=limit)

    def failure_repositories(
        self,
        group_id: str,
        *,
        query: str | None = None,
        signature_id: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> FleetFailureRepositoryPage:
        return self._journal.read_view().failure_repositories(
            group_id,
            query=query,
            signature_id=signature_id,
            offset=offset,
            limit=limit,
        )

    def game_tape_comparison(self, fleet_run_id: str, baseline_run_id: str) -> GameTapeRunDiff:
        tapes = _game_tape_comparison_cut(self._journal, baseline_run_id, fleet_run_id)
        return compare_game_tapes(*tapes)

    def game_tape_performance(
        self, fleet_run_id: str, *, sample_limit: int = 10
    ) -> GameTapePerformanceReport:
        current, prior = _game_tape_history_cut(
            self._journal, fleet_run_id, sample_limit=sample_limit
        )
        return project_game_tape_performance(current, prior, sample_limit=sample_limit)

    def game_tape_failure_history(
        self,
        fleet_run_id: str,
        *,
        repository_id: str,
        step_id: str,
        sample_limit: int = 10,
    ) -> GameTapeFailureHistory:
        current, prior, later = _game_tape_history_window(
            self._journal, fleet_run_id, sample_limit=sample_limit
        )
        return project_game_tape_failure_history(
            current,
            prior,
            later,
            repository_id=repository_id,
            step_id=step_id,
            sample_limit=sample_limit,
        )

    def events(
        self, fleet_run_id: str, after: int = 0, *, limit: int | None = None
    ) -> FleetRunEventBatch:
        return self._journal.run_events(fleet_run_id, after, limit=limit)

    def game_tape_live_output(
        self, fleet_run_id: str, *, after: int = 0
    ) -> GameTapeLiveOutputBatch:
        # While this exact process owns the active worker, Game Tape live output is
        # process-local presentation state. Do not poll the durable journal merely to
        # render progress: certification preparation already exercises the control DB
        # heavily and --wait must not create a second reconnect loop beside the worker.
        if self._supervisor.is_active(fleet_run_id):
            return self._live_output.snapshot(fleet_run_id, after=after)
        return read_game_tape_live_output(
            self._live_output,
            self.get(fleet_run_id),
            after=after,
            diagnostics=self._diagnostics,
        )

    def transient_run_activity(
        self, fleet_run_id: str, *, after: int = 0
    ) -> tuple[int, tuple[tuple[int, str], ...]]:
        """Return bounded process-local preparation activity; never durable run truth."""

        if not self._supervisor.is_active(fleet_run_id):
            # Preserve invalid/terminal identity validation once process-local ownership
            # has ended. Active progress itself must not become a durable polling loop.
            self.get(fleet_run_id)
        return self._live_output.activity_snapshot(fleet_run_id, after=after)

    def wait_for_terminal(
        self,
        fleet_run_id: str,
        *,
        timeout: float | None = None,
    ) -> FleetRunDetail:
        """Wait on process-local ownership first; read durable terminal state once afterward."""

        if self._supervisor.is_active(fleet_run_id):
            if not self._supervisor.wait_until_inactive(fleet_run_id, timeout=timeout):
                raise TimeoutError(f"Timed out waiting for Fleet run {fleet_run_id}")
        # The supervisor owns liveness and process-local failure only. Successful
        # completion is never copied into a second in-memory run representation:
        # terminal truth is reconstructed once from the durable Journal authority.
        failure = self._supervisor.take_failure(fleet_run_id)
        if failure is not None:
            raise StateConflict(
                f"Fleet run {fleet_run_id} background worker failed before durable terminal "
                f"state [{failure.code}/{failure.stage.value}]: {failure.message}"
            )
        # A normal worker commits terminal state before the supervisor can publish
        # inactivity. Re-prove one fresh durable cut before declaring the invariant
        # broken; never spin/poll SQLite while execution is live.
        detail = self.get(fleet_run_id)
        if detail.run.status.terminal:
            return detail
        raise StateConflict(
            f"Fleet run {fleet_run_id} is not active in this process, published no "
            "background failure, and did not reach a terminal state"
        )

    def quiesce(self) -> None:
        self._supervisor.quiesce()

    def cancel_active_for_shutdown(self) -> FleetRunState | None:
        """Cancel only work submitted to this runtime's original supervisor."""
        active = self._journal.active_run()
        if active is None or active.status.terminal:
            return active
        if not self._supervisor.is_active(active.fleet_run_id):
            return None
        try:
            return self.cancel(active.fleet_run_id)
        except InvalidRunTransition:
            return self.get(active.fleet_run_id).run

    def shutdown(self, *, wait: bool = True) -> None:
        self._supervisor.shutdown(wait=wait)

    def _submit(
        self,
        fleet_run_id: str,
        ticket: ExecutionTicket,
    ) -> None:
        try:
            self._supervisor.submit(
                fleet_run_id,
                lambda: self._run_execution.execute(ticket),
            )
        except BaseException as exc:
            # Submission rejection happens after admission created both RUN_ADMITTED
            # and the exact Fleet lease.  Resolve both facts through the admitted
            # owner in one transaction; never terminalize and release separately.
            self._lifecycle.finish_admitted(
                ticket,
                FleetRunTerminalIntent.FAIL,
                FleetRunFailure.from_exception(
                    exc,
                    stage=FleetRunFailureStage.SUBMISSION,
                ),
            )
            raise


def _game_tape_comparison_cut(
    journal: Journal, baseline_run_id: str, fleet_run_id: str
) -> tuple[GameTape, GameTape]:
    view = journal.read_view()
    baseline_detail = view.required_run_detail(baseline_run_id)
    if fleet_run_id == baseline_run_id:
        tape = baseline_detail.game_tape
        return tape, tape
    compared_detail = view.required_run_detail(fleet_run_id)
    return baseline_detail.game_tape, compared_detail.game_tape


def _game_tape_history_cut(
    journal: Journal, fleet_run_id: str, *, sample_limit: int = 10
) -> tuple[GameTape, tuple[GameTape, ...]]:
    current, prior, _ = _run_history_window(
        journal, fleet_run_id, before_limit=sample_limit, after_limit=0
    )
    return current.game_tape, tuple(detail.game_tape for detail in prior)


def _game_tape_history_window(
    journal: Journal, fleet_run_id: str, *, sample_limit: int = 10
) -> tuple[GameTape, tuple[GameTape, ...], tuple[GameTape, ...]]:
    current, prior, later = _run_history_window(
        journal,
        fleet_run_id,
        before_limit=sample_limit,
        after_limit=sample_limit,
    )
    return (
        current.game_tape,
        tuple(detail.game_tape for detail in prior),
        tuple(detail.game_tape for detail in later),
    )


def _run_history_window(
    journal: Journal,
    fleet_run_id: str,
    *,
    before_limit: int,
    after_limit: int,
) -> tuple[FleetRunDetail, tuple[FleetRunDetail, ...], tuple[FleetRunDetail, ...]]:
    return journal.read_view().run_detail_window(
        fleet_run_id, before_limit=before_limit, after_limit=after_limit
    )


def _requested_repository_scope(
    intent: ResolvedCapabilityIntent,
    *,
    repository_ids: set[str] | None,
    all_repositories: bool,
) -> FleetRepositoryScope:
    try:
        return requested_fleet_repository_scope(
            intent.target,
            repository_ids=repository_ids,
            all_repositories=all_repositories,
        )
    except InvalidRepositoryScopeRequest as exc:
        raise StateConflict(str(exc)) from exc


def _rerun_run(
    manager: FleetRunControlPlane,
    fleet_run_id: str,
    *,
    new_fleet_run_id: str,
) -> FleetRunState:
    if fleet_run_id == new_fleet_run_id:
        raise StateConflict("A Fleet run cannot be rerun into its own identity")
    with manager._lifecycle.serialize_admission(f"rerun:{fleet_run_id}"):
        view = manager._journal.read_view()
        detail = view.required_run_detail(fleet_run_id)
        if detail.run.fleet_run_id != fleet_run_id:
            raise JournalConflict(
                "Fleet rerun parent lookup did not return the enrolled Run identity"
            )
        if not detail.run.status.terminal:
            raise StateConflict("Only a terminal Fleet run may be run again")
        if isinstance(detail.execution, DirectFleetExecutionAdmission) and (
            detail.execution.resource_envelope is not None
        ):
            raise StateConflict(
                "A budgeted Run requires a fresh native envelope; repeat the original 'oh-goon run run.yml'"
            )
        # A Run-history rerun starts from captured durable intent, not from a new
        # Plan request.  Before resolving capability policy or deriving a fresh
        # repository scope, ask the *original* lifecycle owner whether replay
        # ingress is allowed.  Authenticated Fleet Plan lineage remains denied
        # until its native issuer + atomic Journal cutover actually exist; a
        # successfully enrolled ordinary Plan is not a rerun authorization.
        # Non-Plan and legacy reruns retain their existing behavior.
        manager._lifecycle.require_fleet_prework_available(
            detail.run.target,
            new_fleet_run_id,
            rerun_from_run_id=fleet_run_id,
        )
        intent = resolve_bound_capability_intent(detail.run.capability, detail.run.target)
        if not intent.definition.rerun_supported:
            raise StateConflict(
                f"{detail.run.capability.action.value} is not repeatable through Run history"
            )
        try:
            # _start_intent owns the idempotency lookup and JournalConflict replay.
            # Do not duplicate the same durable read at the public rerun boundary.
            return manager._start_intent(
                intent,
                repository_scope=rerun_repository_scope(detail),
                fleet_run_id=new_fleet_run_id,
                source=FleetRunSource.MANUAL,
                readmitted_from_run_id=None,
                rerun_from_run_id=fleet_run_id,
            )
        except JournalConflict as error:
            # JournalConflict is the storage-level identity invariant. Rerun owns a
            # public child-command boundary, so an already-bound child identity is a
            # domain state conflict rather than a new API/storage error class.
            raise StateConflict(
                f"Fleet rerun child identity is already bound to another execution: {new_fleet_run_id}"
            ) from error


def _readmit_run(
    manager: FleetRunControlPlane,
    failed_fleet_run_id: str,
    *,
    new_fleet_run_id: str,
) -> FleetRunState:
    if failed_fleet_run_id == new_fleet_run_id:
        raise ExecutorReadmissionNotAllowed(
            "A Fleet run cannot be re-admitted into its own identity"
        )
    with manager._lifecycle.serialize_admission(f"readmission:{failed_fleet_run_id}"):
        view = manager._journal.read_view()
        detail = view.required_run_detail(failed_fleet_run_id)
        if detail.run.fleet_run_id != failed_fleet_run_id:
            raise JournalConflict(
                "Fleet re-admission parent lookup did not return the enrolled Run identity"
            )
        failure = getattr(detail.run, "failure", None)
        if (
            detail.run.status is not FleetRunStatus.FAILED
            or failure is None
            or failure.code != "EXECUTOR_PLACEMENT_LOST"
            or detail.run.source is not FleetRunSource.CICD
            or not isinstance(detail.run.target, FleetPlanTarget)
            or not isinstance(detail.run.target.plan.execution, CicdExecutionPolicy)
        ):
            raise ExecutorReadmissionNotAllowed(
                "Only a failed strict CI/CD run with CICD provenance and EXECUTOR_PLACEMENT_LOST may be explicitly re-admitted"
            )
        if detail.run.progress.completed > 0:
            raise ExecutorReadmissionNotAllowed(
                "Fleet placement-loss re-admission is blocked after any repository effect durably completed"
            )
        existing_child = view.readmission_child(failed_fleet_run_id)
        if existing_child is not None:
            # The durable parent→child lineage owns idempotency. A browser reload may
            # generate a fresh command identity, but it must never create or compete
            # with a second readmission child for the same failed parent. Prove the
            # projected child belongs to this *captured* parent before returning a
            # replay result: a mismatched child must not become an alternate Run
            # lookup/issuer just because the read index supplied its identity.
            require_fleet_run_detail_binding(
                existing_child,
                source=detail.run.source,
                capability=detail.run.capability,
                target=detail.run.target,
                repository_scope=detail.run.repository_scope,
                readmitted_from_run_id=failed_fleet_run_id,
                rerun_from_run_id=None,
            )
            if (
                existing_child.run.fleet_run_id == failed_fleet_run_id
                or existing_child.run.targets != detail.run.targets
            ):
                raise JournalConflict(
                    "Fleet re-admission child lookup is not bound to the parent "
                    "captured repository identities"
                )
            return existing_child.run
        # A missing child requests *new* authenticated replay authority, unlike
        # the read-only idempotent child lookup above. Ask the original lifecycle
        # owner before deriving capability intent or reaching Plan/repository
        # planning. An enrolled ordinary strict Run is not a placement-loss
        # readmission entitlement; the native replay issuer + Journal cutover
        # are still absent. _start_intent/admit retain their own final checks.
        manager._lifecycle.require_fleet_prework_available(
            detail.run.target,
            new_fleet_run_id,
            readmitted_from_run_id=failed_fleet_run_id,
        )
        intent = resolve_bound_capability_intent(detail.run.capability, detail.run.target)
        return manager._start_intent(
            intent,
            repository_scope=detail.run.repository_scope,
            fleet_run_id=new_fleet_run_id,
            source=detail.run.source,
            readmitted_from_run_id=failed_fleet_run_id,
            rerun_from_run_id=None,
        )


def _matching_existing_run(
    journal: Journal,
    fleet_run_id: str,
    intent: ResolvedCapabilityIntent,
    *,
    repository_scope: FleetRepositoryScope,
    source: FleetRunSource,
    readmitted_from_run_id: str | None,
    rerun_from_run_id: str | None,
    requested_priority: SchedulingPriority = SchedulingPriority.NORMAL,
) -> FleetRunState | None:
    detail = journal.read_view().run_detail(fleet_run_id)
    if detail is None:
        return None
    target = intent.target
    if isinstance(target, FleetPlanTarget) and isinstance(detail.run.target, FleetPlanTarget):
        target = with_retained_project_deliveries(target, detail.run.target)
        target = with_retained_agent_deliveries(target, detail.run.target)
    require_fleet_run_detail_binding(
        detail,
        source=source,
        capability=intent.definition.durable_snapshot(),
        target=target,
        repository_scope=repository_scope,
        readmitted_from_run_id=readmitted_from_run_id,
        rerun_from_run_id=rerun_from_run_id,
    )
    require_fleet_run_scheduling_binding(detail, requested_priority)
    return detail.run
