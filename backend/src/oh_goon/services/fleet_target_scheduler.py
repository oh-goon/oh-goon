from __future__ import annotations

import sqlite3
from collections.abc import Callable
from oh_goon.core.ports.local_lace_intent import LocalLaceIntentReservation
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait

from oh_goon.core.capability_model import ResolvedCapabilityIntent
from oh_goon.core.models.capability import CapabilitySnapshot
from oh_goon.core.models.fleet_plan_evidence import PlanIsolationBackendEvidence
from oh_goon.core.models.repository import IncludedRepositoryCatalogEntry
from oh_goon.core.models.run import FleetRunDetail
from oh_goon.core.models.run_admission import PlanRepositoryAdmissionEvidence
from oh_goon.runtime.execution import ExecutionSession
from oh_goon.services.repository_effect_execution import RepositoryEffectExecution


def _emit_activity(activity: Callable[[str], None] | None, message: str) -> None:
    if activity is not None:
        activity(message)


def _execute_target(
    effects: RepositoryEffectExecution,
    intent: ResolvedCapabilityIntent,
    capability: CapabilitySnapshot,
    entry: IncludedRepositoryCatalogEntry,
    session: ExecutionSession,
    isolation_backend: PlanIsolationBackendEvidence | None,
    repository_admission: PlanRepositoryAdmissionEvidence | None,
    validate_authority_in: Callable[[sqlite3.Connection], None] | None,
    activity: Callable[[str], None] | None,
    local_lace_intent_for: Callable[..., LocalLaceIntentReservation] | None = None,
    admitted_run: FleetRunDetail | None = None,
) -> None:
    kwargs: dict[str, object] = {}
    if validate_authority_in is not None:
        kwargs["validate_authority_in"] = validate_authority_in
    if activity is not None:
        kwargs["activity"] = activity
    if local_lace_intent_for is not None:
        kwargs["local_lace_intent_for"] = local_lace_intent_for
    if admitted_run is not None:
        kwargs["admitted_run"] = admitted_run
    _emit_activity(activity, f"Repository target worker entered for {entry.repository.path.name}")
    # Parallel target workers run on distinct threads. Give each thread one admitted
    # native SQLite handle while preserving transaction-scoped authority. Sequential
    # execution nests into the Fleet worker's existing connection scope.
    with session.execution_connection_scope():
        _emit_activity(
            activity, f"Repository execution authority ready for {entry.repository.path.name}"
        )
        effects.execute_fleet_target(
            intent,
            capability,
            entry,
            session,
            isolation_backend,
            repository_admission=repository_admission,
            **kwargs,
        )


class FleetTargetScheduler:
    """Own only Fleet target ordering/parallelism; repository effects live elsewhere."""

    def __init__(self, effects: RepositoryEffectExecution) -> None:
        self._effects = effects

    def execute(
        self,
        intent: ResolvedCapabilityIntent,
        capability: CapabilitySnapshot,
        targets: list[IncludedRepositoryCatalogEntry],
        session: ExecutionSession,
        *,
        parallel_safe: bool,
        max_parallel: int | None = None,
        isolation_backend: PlanIsolationBackendEvidence | None = None,
        repository_admissions: tuple[PlanRepositoryAdmissionEvidence, ...],
        validate_authority_in: Callable[[sqlite3.Connection], None] | None = None,
        activity: Callable[[str], None] | None = None,
        local_lace_intent_for: Callable[..., LocalLaceIntentReservation] | None = None,
        admitted_run: FleetRunDetail | None = None,
    ) -> None:
        admitted_by_repository = {item.repository_id: item for item in repository_admissions}

        def execute_target(entry: IncludedRepositoryCatalogEntry) -> None:
            _execute_target(
                self._effects,
                intent,
                capability,
                entry,
                session,
                isolation_backend,
                admitted_by_repository.get(entry.repository.repository_id),
                validate_authority_in,
                activity,
                local_lace_intent_for,
                admitted_run,
            )

        if not parallel_safe:
            for entry in targets:
                execute_target(entry)
            return

        errors: dict[int, Exception] = {}
        worker_limit = max_parallel or 4
        next_index = 0
        settled_success: set[int] = set()
        settled_prefix = 0
        in_flight: dict[Future[None], int] = {}

        def advance_settled_prefix() -> None:
            nonlocal settled_prefix
            while settled_prefix in settled_success:
                settled_success.remove(settled_prefix)
                settled_prefix += 1

        def submit_window(pool: ThreadPoolExecutor) -> None:
            nonlocal next_index
            window_end = min(len(targets), settled_prefix + worker_limit)
            while not errors and next_index < window_end:
                index = next_index
                next_index += 1
                in_flight[pool.submit(execute_target, targets[index])] = index

        with ThreadPoolExecutor(max_workers=worker_limit) as pool:
            submit_window(pool)
            while in_flight:
                completed, _pending = wait(tuple(in_flight), return_when=FIRST_COMPLETED)
                for future in completed:
                    index = in_flight.pop(future)
                    try:
                        future.result()
                    except Exception as exc:
                        errors[index] = exc
                    else:
                        settled_success.add(index)
                advance_settled_prefix()
                submit_window(pool)
        if errors:
            raise errors[min(errors)]
