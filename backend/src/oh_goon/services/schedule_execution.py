from __future__ import annotations

from oh_goon.core.errors import FleetDomainError, RunNotFound, ScheduleExecutionStale
from oh_goon.core.product_identity import schedule_repository_fleet_run_id
from oh_goon.core.run_identity import require_fleet_run_binding
from oh_goon.core.models.run_admission import ExplicitRepositoryScope
from oh_goon.core.models.schedule import ScheduleFailure
from oh_goon.core.models.schedule_execution import RunningScheduleRun, ScheduleRunRecord
from oh_goon.core.schedule_transitions import (
    begin_schedule_run,
    fail_schedule_run,
    mark_repository_not_started,
    observe_repository_run,
    settle_if_complete,
)
from oh_goon.core.time import utc_now
from oh_goon.core.vocabulary import (
    FleetRunSource,
    ScheduleFailureStage,
    ScheduleRepositoryRunStatus,
)
from oh_goon.runtime.schedule_executions import ScheduleExecutionStore
from oh_goon.services.fleet_run_control_plane import FleetRunControlPlane
from oh_goon.services.schedule_admission import ScheduleAdmissionPlan


class ScheduleExecutionEngine:
    """Fan one occurrence into independent repository Runs through the existing Fleet executor."""

    def __init__(self, executions: ScheduleExecutionStore, runs: FleetRunControlPlane) -> None:
        self._executions = executions
        self._runs = runs

    def start(self, admission: ScheduleAdmissionPlan, *, schedule_run_id: str) -> ScheduleRunRecord:
        initial = begin_schedule_run(
            schedule_run_id=schedule_run_id,
            schedule_id=admission.schedule_id,
            intent=admission.intent,
            started_at=utc_now(),
        )
        self._executions.insert(initial, expected_durable_revision=admission.durable_revision)
        return self.advance(schedule_run_id)

    def advance(
        self, schedule_run_id: str, *, allow_effect_admission: bool = True
    ) -> ScheduleRunRecord:
        current = self._executions.get(schedule_run_id)
        if current.status.terminal or not isinstance(current, RunningScheduleRun):
            return current
        try:
            running = next(
                (
                    x
                    for x in current.repository_runs
                    if x.status is ScheduleRepositoryRunStatus.RUNNING
                ),
                None,
            )
            if running is not None:
                return self._observe_running(current, running.repository_id, running.fleet_run_id)
            pending = next(
                (
                    x
                    for x in current.repository_runs
                    if x.status is ScheduleRepositoryRunStatus.PENDING
                ),
                None,
            )
            if pending is None:
                return self._cas(current, settle_if_complete(current, finished_at=utc_now()))
            if not allow_effect_admission:
                return current
            return self._start_repository(current, pending.repository_id)
        except ScheduleExecutionStale:
            return self._executions.get(schedule_run_id)

    def advance_all(self, *, allow_effect_admission: bool = True) -> None:
        for execution in self._executions.active():
            try:
                self.advance(
                    execution.schedule_run_id, allow_effect_admission=allow_effect_admission
                )
            except FleetDomainError as exc:
                self._fail_latest(
                    execution.schedule_run_id,
                    ScheduleFailure.from_exception(exc, stage=ScheduleFailureStage.ADVANCE),
                )

    def _observe_running(
        self, current: RunningScheduleRun, repository_id: str, fleet_run_id: str | None
    ) -> ScheduleRunRecord:
        if fleet_run_id is None:
            raise AssertionError("Running Schedule repository must have Fleet Run identity")
        try:
            detail = self._runs.get(fleet_run_id)
        except RunNotFound as exc:
            # CO-R2a: RUNNING is committed IN the same cut as RUN_ADMITTED.
            # If the Journal cannot read its child on restart, preserve the
            # binding and block further admissions; do not silently turn an
            # already-admitted child into NOT_STARTED.
            raise ScheduleExecutionStale(
                "Durably bound scheduled Fleet Run is missing; original recovery required"
            ) from exc
        require_fleet_run_binding(
            detail.run,
            source=FleetRunSource.SCHEDULED,
            capability=current.intent.capability,
            target=current.intent.target,
            repository_scope=ExplicitRepositoryScope(repository_ids=(repository_id,)),
        )
        if not detail.run.status.terminal:
            return current
        return self._cas(
            current, observe_repository_run(current, repository_id, detail, observed_at=utc_now())
        )

    def _start_repository(
        self, current: RunningScheduleRun, repository_id: str
    ) -> ScheduleRunRecord:
        fleet_run_id = schedule_repository_fleet_run_id(current.schedule_run_id, repository_id)
        scope = ExplicitRepositoryScope(repository_ids=(repository_id,))
        try:
            self._runs.start_bound(
                current.intent.capability,
                target=current.intent.target,
                repository_scope=scope,
                fleet_run_id=fleet_run_id,
                source=FleetRunSource.SCHEDULED,
                schedule_run_id=current.schedule_run_id,
                expected_repository_instance_ids={
                    repository_id: current.intent.repository_instance_ids[repository_id]
                },
            )
        except FleetDomainError as exc:
            # Admission failure belongs only to this target; other targets remain eligible.
            return self._cas(
                current,
                mark_repository_not_started(
                    current,
                    repository_id,
                    ScheduleFailure.from_exception(exc, stage=ScheduleFailureStage.ADMISSION),
                ),
            )
        # CO-R2a: RUN_ADMITTED and its exact occurrence RUNNING binding are now
        # published in the ORIGINAL Fleet admission write cut. This service
        # never writes a second post-admission publication. A lost ACK after
        # commit is recovered by the existing advance/observe path.
        bound = self._executions.get(current.schedule_run_id)
        if not isinstance(bound, RunningScheduleRun) or not any(
            item.repository_id == repository_id
            and item.status is ScheduleRepositoryRunStatus.RUNNING
            and item.fleet_run_id == fleet_run_id
            for item in bound.repository_runs
        ):
            raise ScheduleExecutionStale("Scheduled Fleet admission returned without durable child binding")
        return bound

    def _fail_latest(self, schedule_run_id: str, failure: ScheduleFailure) -> ScheduleRunRecord:
        current = self._executions.get(schedule_run_id)
        if not isinstance(current, RunningScheduleRun):
            return current
        return self._fail(current, failure)

    def _fail(self, current: RunningScheduleRun, failure: ScheduleFailure) -> ScheduleRunRecord:
        return self._cas(current, fail_schedule_run(current, failure, finished_at=utc_now()))

    def _cas(self, current: ScheduleRunRecord, next_record: ScheduleRunRecord) -> ScheduleRunRecord:
        if next_record == current:
            return current
        try:
            return self._executions.compare_and_set(current, next_record)
        except ScheduleExecutionStale:
            return self._executions.get(current.schedule_run_id)
