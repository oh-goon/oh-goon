from __future__ import annotations

from oh_goon.core.capability_registry import resolve_bound_capability_intent
from oh_goon.core.models.diagnostic import DiagnosticEvent
from oh_goon.core.models.repository import IncludedRepositoryCatalogEntry
from oh_goon.core.models.run import FleetRunDetail, FleetRunFailure
from oh_goon.core.models.run_admission import StrictFleetExecutionAdmission
from oh_goon.core.ports.diagnostics import DiagnosticEmitter
from oh_goon.core.vocabulary import FleetRunFailureStage, FleetRunTerminalIntent, FleetScope
from oh_goon.engine.errors import OperationCancelledError
from oh_goon.runtime.database import DatabasePathIdentityLost
from oh_goon.runtime.execution import ExecutionSession, ExecutionTicket
from oh_goon.runtime.game_tape_live_output import GameTapeLiveOutputStore
from oh_goon.services.fleet_run_lifecycle import FleetRunLifecycle
from oh_goon.services.fleet_caller_file_freshness import require_target_caller_file_fresh
from oh_goon.services.fleet_target_scheduler import FleetTargetScheduler


class FleetRunExecution:
    """Own one admitted FleetRun through atomic terminal fact + lease release."""

    def __init__(
        self,
        lifecycle: FleetRunLifecycle,
        targets: FleetTargetScheduler,
        live_output: GameTapeLiveOutputStore,
        diagnostics: DiagnosticEmitter | None = None,
    ) -> None:
        self._lifecycle = lifecycle
        self._targets = targets
        self._live_output = live_output
        self._diagnostics = diagnostics

    def execute(
        self,
        ticket: ExecutionTicket,
    ) -> FleetRunDetail:
        # One worker thread owns one admitted native SQLite handle for the complete run.
        # Transactions and effect fences remain separate durable cuts; only reconnect churn
        # is removed. Parallel target workers get their own thread-local scopes when needed.
        if ticket.recovery_only:
            from oh_goon.core.errors import OwnershipLost
            raise OwnershipLost("recovery-only custody forbids Run/Step execution")
        with self._lifecycle.execution_connection_scope():
            session = self._lifecycle.activate(ticket)
            fleet_run_id = session.require_fleet_run_id()
            self._observe_live_output(
                lambda: self._live_output.open_run(fleet_run_id),
                fleet_run_id=fleet_run_id,
                operation="open_run",
            )
            try:
                terminal, failure = self._run(session)
            except BaseException as exc:
                self._seal_live_output(fleet_run_id)
                if isinstance(exc, DatabasePathIdentityLost):
                    # Durable terminalization requires the same SQLite authority that just
                    # failed identity proof. Do not mask the primary database failure with
                    # a second fresh-open failure or pretend a terminal fact was committed.
                    raise
                self._lifecycle.finish(
                    session,
                    FleetRunTerminalIntent.FAIL,
                    FleetRunFailure.from_exception(exc, stage=FleetRunFailureStage.EXECUTION),
                )
                raise
            self._seal_live_output(fleet_run_id)
            return self._lifecycle.finish_detail(session, terminal, failure)

    def resume_local_lace_takeover(self, ticket: ExecutionTicket, coordinator, *,
                                   intent_run_seq: int) -> tuple[int, int]:
        """Explicit production-worker recovery; never scheduled from app startup.

        Activation is the same original Fleet lease cut used for ordinary work.
        This method neither starts a Run nor executes a Step or native deletion.
        An unqualified scan can only append a cleanup-forbidden receipt.
        """
        from oh_goon.services.fleet_local_lace_takeover import OriginalLocalLaceTakeoverCoordinator
        from oh_goon.runtime.enforcer_lace_journal import CausalJournalError

        if (type(ticket) is not ExecutionTicket
                or type(coordinator) is not OriginalLocalLaceTakeoverCoordinator
                or type(intent_run_seq) is not int or intent_run_seq < 2):
            raise CausalJournalError("original Fleet recovery ticket, coordinator and intent required")
        with self._lifecycle.execution_connection_scope():
            session = (self._lifecycle.activate_recovery(ticket) if ticket.recovery_only
                       else self._lifecycle.activate(ticket))
            expected = self._lifecycle.execution_authority(session)
            owner = self._lifecycle.local_lace_recovery_owner(session, expected)
            # The original executor inventory, not the caller, owns native binary
            # and resource paths. Refuse a forged coordinator before any scan.
            backend = self._lifecycle.original_local_lace_recovery_backend(expected)
            return coordinator.resume(owner, intent_run_seq=intent_run_seq, original_backend=backend)

    def _activity(self, fleet_run_id: str, message: str) -> None:
        self._observe_live_output(
            lambda: self._live_output.append_activity(fleet_run_id, message),
            fleet_run_id=fleet_run_id,
            operation="append_run_activity",
        )

    def _seal_live_output(self, fleet_run_id: str) -> None:
        """Close the process-local producer before publishing the durable terminal fact."""
        self._observe_live_output(
            lambda: self._live_output.seal(fleet_run_id),
            fleet_run_id=fleet_run_id,
            operation="seal_run",
        )

    def _observe_live_output(
        self, observe, *, fleet_run_id: str, operation: str
    ) -> None:
        """Game Tape availability must never control the Fleet execution lifecycle."""
        try:
            observe()
        except Exception as exc:
            if self._diagnostics is not None:
                self._diagnostics.emit(
                    DiagnosticEvent.from_exception(
                        exc,
                        severity="WARNING",
                        source="game_tape_live_output",
                        owner="FleetRunExecution",
                        operation=operation,
                        execution_affected=False,
                        diagnostic_capability_affected="live_output_capture",
                        failure_class="diagnostic_degraded",
                        fleet_run_id=fleet_run_id,
                    )
                )

    def _run(
        self,
        session: ExecutionSession,
    ) -> tuple[FleetRunTerminalIntent, FleetRunFailure | None]:
        try:
            authority = self._lifecycle.execution_authority(session)
            intent = resolve_bound_capability_intent(authority.capability, authority.target)
            targets = [
                IncludedRepositoryCatalogEntry(
                    scope=FleetScope.INCLUDED,
                    repository=repository,
                )
                for repository in authority.targets
            ]
            # Queue-time source change fails before RUN_STARTED and all repository effects.
            require_target_caller_file_fresh(authority.target)
            self._lifecycle.begin(session)
            fleet_run_id = session.require_fleet_run_id()
            self._activity(fleet_run_id, "Run started; preparing repository execution")
            if session.cancel_requested():
                return FleetRunTerminalIntent.CANCEL, None
            execution = authority.execution
            if isinstance(execution, StrictFleetExecutionAdmission):
                parallel_safe = execution.placement.executor_capacity.admitted_parallelism > 1
                max_parallel = execution.placement.executor_capacity.admitted_parallelism
                isolation_backend = execution.placement.isolation_backend
            else:
                parallel_safe = intent.parallel_safe
                max_parallel = intent.max_parallel
                isolation_backend = None
            local_lace_intent_for = self._lifecycle.local_lace_intent_for(session, authority)
            self._targets.execute(
                intent,
                authority.capability,
                targets,
                session,
                parallel_safe=parallel_safe,
                max_parallel=max_parallel,
                isolation_backend=isolation_backend,
                local_lace_intent_for=local_lace_intent_for,
                repository_admissions=authority.repository_admissions,
                # Non-Plan Fleet capabilities never receive Plan-only TL-1 context.
                admitted_run=(
                    authority.admitted_run
                    if isinstance(execution, StrictFleetExecutionAdmission) else None
                ),
                validate_authority_in=lambda connection: self._lifecycle.require_execution_authority_in(
                    connection, fleet_run_id, authority
                ),
                activity=lambda message: self._activity(fleet_run_id, message),
            )
            if session.cancel_requested():
                return FleetRunTerminalIntent.CANCEL, None
            return FleetRunTerminalIntent.COMPLETE, None
        except DatabasePathIdentityLost:
            # Database identity loss is not an ordinary execution failure that can be
            # converted into a durable terminal row: the database needed for that row
            # is precisely the authority that is no longer trusted.
            raise
        except OperationCancelledError:
            return FleetRunTerminalIntent.CANCEL, None
        except Exception as exc:
            return (
                FleetRunTerminalIntent.FAIL,
                FleetRunFailure.from_exception(exc, stage=FleetRunFailureStage.EXECUTION),
            )
