from __future__ import annotations

from datetime import datetime
from typing import Any

from oh_goon.core.capability_model import ResolvedCapabilityIntent
from oh_goon.core.capability_registry import (
    resolve_bound_capability_intent,
    resolve_capability_intent,
)
from oh_goon.core.errors import (
    ExecutorReadmissionNotAllowed,
    FleetPlanInvalid,
    InvalidRunTransition,
    JournalConflict,
    StateConflict,
)
from oh_goon.core.game_tape_comparison import compare_game_tapes
from oh_goon.core.game_tape_failure_history import project_game_tape_failure_history
from oh_goon.core.game_tape_performance import project_game_tape_performance
from oh_goon.core.models.capability import CapabilitySnapshot
from oh_goon.core.models.execution_observability import ExecutionTimeBucket, FleetExecutionTimeline
from oh_goon.core.models.fleet_failures import (
    FleetFailureGroupPage,
    FleetFailureInventory,
    FleetFailureRepositoryPage,
)
from oh_goon.core.models.fleet_plan_execution_policy import CicdExecutionPolicy
from oh_goon.core.models.game_tape import GameTape, GameTapeRunDiff
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
    ExplicitRepositoryScope,
    FleetRepositoryScope,
    InvalidRepositoryScopeRequest,
    requested_fleet_repository_scope,
)
from oh_goon.core.models.run_events import FleetRunEventBatch
from oh_goon.core.models.scheduling import SchedulingPriority
from oh_goon.core.models.target import FleetPlanTarget, OperationTarget
from oh_goon.core.ports.diagnostics import DiagnosticEmitter
from oh_goon.core.product_identity import new_fleet_run_id
from oh_goon.core.projections.agent_campaign import project_agent_campaign
from oh_goon.core.run_identity import (
    require_fleet_run_detail_binding,
    require_fleet_run_scheduling_binding,
)
from oh_goon.core.vocabulary import (
    FleetRunFailureStage,
    FleetRunOutcome,
    FleetRunSource,
    FleetRunStatus,
    FleetRunTerminalIntent,
    OperationAction,
)
from oh_goon.runtime.execution import ExecutionTicket, RunExecutionSupervisor
from oh_goon.runtime.game_tape_live_output import GameTapeLiveOutputStore
from oh_goon.runtime.journal import Journal
from oh_goon.runtime.schedule_executions import ScheduleExecutionStore
from oh_goon.services.fleet_admission import FleetAdmissionPlanner
from oh_goon.services.fleet_caller_file_freshness import require_target_caller_file_fresh
from oh_goon.services.fleet_rerun_scope import rerun_repository_scope
from oh_goon.services.fleet_run_execution import FleetRunExecution
from oh_goon.services.fleet_run_lifecycle import FleetRunLifecycle
from oh_goon.services.game_tape_live_output import read_game_tape_live_output


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
    ) -> None:
        self._admission = admission
        self._journal = journal
        self._lifecycle = lifecycle
        self._supervisor = supervisor
        self._run_execution = run_execution
        self._live_output = live_output
        self._diagnostics = diagnostics
        self._schedule_executions = schedule_executions

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
        self, ticket: ExecutionTicket, coordinator, *, intent_run_seq: int
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
    ) -> FleetRunState:
        """Start only a Fleet Plan target already frozen by FleetPlanService."""
        intent = resolve_capability_intent(OperationAction.EXECUTE_PLAN, target)
        return self._start_intent(
            intent,
            repository_scope=_requested_repository_scope(
                intent, repository_ids=repository_ids, all_repositories=all_repositories
            ),
            fleet_run_id=fleet_run_id,
            expected_repository_instance_ids=expected_repository_instance_ids,
            source=source,
            readmitted_from_run_id=None,
            rerun_from_run_id=None,
            requested_priority=requested_priority,
        )

    def repository_instance_ids_for_prepared_plan(
        self, target: FleetPlanTarget, *, repository_ids: set[str]
    ) -> dict[str, str]:
        intent = resolve_capability_intent(OperationAction.EXECUTE_PLAN, target)
        scope = _requested_repository_scope(
            intent, repository_ids=repository_ids, all_repositories=False
        )
        return self._admission.repository_instance_ids(intent, repository_scope=scope)

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
                    intent,
                    repository_scope=repository_scope,
                    source=source,
                    readmitted_from_run_id=readmitted_from_run_id,
                    rerun_from_run_id=rerun_from_run_id,
                    requested_priority=requested_priority,
                )
                if existing is not None:
                    return existing
                raise
            self._submit(fleet_run_id, ticket)
            return state

    def cancel(self, fleet_run_id: str) -> FleetRunState:
        return self._lifecycle.cancel(fleet_run_id)

    def get(self, fleet_run_id: str) -> FleetRunDetail:
        detail = self._journal.read_view().required_run_detail(fleet_run_id)
        try:
            from oh_goon.integrations.hashmarks.runtime_contract import (
                loaded_hashmarks_producer_provenance,
            )

            provenance = loaded_hashmarks_producer_provenance()
        except ImportError:
            provenance = None
        if provenance is None:
            return detail
        return detail.model_copy(update={"producer_implementation_provenance": provenance})

    def agent_campaign(self, fleet_run_id: str) -> dict[str, Any]:
        """Inspect linked campaign Runs from one verified immutable journal cut."""
        view = self._journal.read_view()
        selected = view.required_run_detail(fleet_run_id)
        target = selected.run.target
        if (
            not isinstance(target, FleetPlanTarget)
            or target.agent_repair is None
            or target.agent_repair.phase is None
        ):
            raise FleetPlanInvalid("Selected Run is not part of a review-repair campaign")
        return project_agent_campaign(
            target.agent_repair.repair_id,
            view.run_details(),
            journal_revision=view.revision,
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
    require_fleet_run_detail_binding(
        detail,
        source=source,
        capability=intent.definition.durable_snapshot(),
        target=intent.target,
        repository_scope=repository_scope,
        readmitted_from_run_id=readmitted_from_run_id,
        rerun_from_run_id=rerun_from_run_id,
    )
    require_fleet_run_scheduling_binding(detail, requested_priority)
    return detail.run
