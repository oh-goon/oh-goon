from __future__ import annotations

from datetime import datetime

from oh_goon.core.models.run import FleetRunDetail, FleetRunFailure
from oh_goon.core.models.run_events import (
    FleetRunEvent,
    RepositoryCompletedEvent,
    RepositoryStartedEvent,
    RunAdmittedEvent,
    RunCancelledEvent,
    RunCancelRequestedEvent,
    RunCompletedEvent,
    RunFailedEvent,
    RunStartedEvent,
    StepAttemptCreatedEvent,
    StepCancelledEvent,
    StepFailedEvent,
    StepReachedEvent,
    StepSkippedEvent,
    StepStartedEvent,
    StepSucceededEvent,
)
from oh_goon.core.product_identity import fleet_step_attempt_id
from oh_goon.core.vocabulary import FleetRunEventType


class CanonicalFleetJournalBuilder:
    """Test-only builder for explicit canonical Fleet journal histories.

    The builder owns only monotonically increasing test sequence allocation and event
    construction.  It deliberately does not validate lifecycle transitions; production
    projection remains the single state machine and rejects impossible histories.
    """

    def __init__(self, detail: FleetRunDetail) -> None:
        run = detail.run
        self._fleet_run_id = run.fleet_run_id
        self._events: list[FleetRunEvent] = [
            RunAdmittedEvent(
                event_type=FleetRunEventType.RUN_ADMITTED,
                sequence=1,
                fleet_run_id=run.fleet_run_id,
                generated_at=run.accepted_at,
                source=run.source,
                target=run.target,
                capability=run.capability,
                repository_scope=run.repository_scope,
                targets=run.targets,
                execution=detail.execution,
                repository_admissions=detail.repository_admissions,
                application_launch=detail.application_launch,
                scheduling=detail.scheduling,
            )
        ]

    def _sequence(self) -> int:
        return len(self._events) + 1

    def run_started(self, at: datetime) -> CanonicalFleetJournalBuilder:
        self._events.append(
            RunStartedEvent(
                event_type=FleetRunEventType.RUN_STARTED,
                sequence=self._sequence(),
                fleet_run_id=self._fleet_run_id,
                generated_at=at,
            )
        )
        return self

    def repository_started(
        self, repository_id: str, operation_id: str, at: datetime
    ) -> CanonicalFleetJournalBuilder:
        self._events.append(
            RepositoryStartedEvent(
                event_type=FleetRunEventType.REPOSITORY_STARTED,
                sequence=self._sequence(),
                fleet_run_id=self._fleet_run_id,
                repository_id=repository_id,
                operation_id=operation_id,
                generated_at=at,
            )
        )
        return self

    def step_reached(
        self,
        repository_id: str,
        operation_id: str,
        step_id: str,
        step_order: int,
        at: datetime,
    ) -> CanonicalFleetJournalBuilder:
        self._events.append(
            StepReachedEvent(
                event_type=FleetRunEventType.STEP_REACHED,
                sequence=self._sequence(),
                fleet_run_id=self._fleet_run_id,
                repository_id=repository_id,
                operation_id=operation_id,
                step_id=step_id,
                step_order=step_order,
                generated_at=at,
            )
        )
        return self

    def step_started(
        self,
        repository_id: str,
        operation_id: str,
        step_id: str,
        step_order: int,
        at: datetime,
    ) -> CanonicalFleetJournalBuilder:
        self._events.append(
            StepStartedEvent(
                event_type=FleetRunEventType.STEP_STARTED,
                sequence=self._sequence(),
                fleet_run_id=self._fleet_run_id,
                repository_id=repository_id,
                operation_id=operation_id,
                step_id=step_id,
                step_order=step_order,
                generated_at=at,
            )
        )
        return self

    def step_attempt_created(
        self,
        repository_id: str,
        operation_id: str,
        step_id: str,
        step_order: int,
        attempt_number: int,
        at: datetime,
    ) -> CanonicalFleetJournalBuilder:
        self._events.append(
            StepAttemptCreatedEvent(
                event_type=FleetRunEventType.STEP_ATTEMPT_CREATED,
                sequence=self._sequence(),
                fleet_run_id=self._fleet_run_id,
                repository_id=repository_id,
                operation_id=operation_id,
                step_id=step_id,
                step_order=step_order,
                attempt_number=attempt_number,
                step_attempt_id=fleet_step_attempt_id(
                    operation_id, step_id, attempt_number
                ),
                generated_at=at,
            )
        )
        return self

    def step_succeeded(self, repository_id: str, operation_id: str, step_id: str, step_order: int, returncode: int, at: datetime) -> CanonicalFleetJournalBuilder:
        self._events.append(StepSucceededEvent(event_type=FleetRunEventType.STEP_SUCCEEDED, sequence=self._sequence(), fleet_run_id=self._fleet_run_id, repository_id=repository_id, operation_id=operation_id, step_id=step_id, step_order=step_order, returncode=returncode, generated_at=at))
        return self

    def step_failed(self, repository_id: str, operation_id: str, step_id: str, step_order: int, returncode: int, at: datetime) -> CanonicalFleetJournalBuilder:
        self._events.append(StepFailedEvent(event_type=FleetRunEventType.STEP_FAILED, sequence=self._sequence(), fleet_run_id=self._fleet_run_id, repository_id=repository_id, operation_id=operation_id, step_id=step_id, step_order=step_order, returncode=returncode, generated_at=at))
        return self

    def step_skipped(self, repository_id: str, operation_id: str, step_id: str, step_order: int, at: datetime) -> CanonicalFleetJournalBuilder:
        self._events.append(StepSkippedEvent(event_type=FleetRunEventType.STEP_SKIPPED, sequence=self._sequence(), fleet_run_id=self._fleet_run_id, repository_id=repository_id, operation_id=operation_id, step_id=step_id, step_order=step_order, generated_at=at))
        return self

    def step_cancelled(self, repository_id: str, operation_id: str, step_id: str, step_order: int, at: datetime) -> CanonicalFleetJournalBuilder:
        self._events.append(StepCancelledEvent(event_type=FleetRunEventType.STEP_CANCELLED, sequence=self._sequence(), fleet_run_id=self._fleet_run_id, repository_id=repository_id, operation_id=operation_id, step_id=step_id, step_order=step_order, generated_at=at))
        return self

    def repository_completed(
        self, operation_id: str, at: datetime
    ) -> CanonicalFleetJournalBuilder:
        self._events.append(
            RepositoryCompletedEvent(
                event_type=FleetRunEventType.REPOSITORY_COMPLETED,
                sequence=self._sequence(),
                fleet_run_id=self._fleet_run_id,
                operation_id=operation_id,
                generated_at=at,
            )
        )
        return self

    def cancel_requested(self, at: datetime) -> CanonicalFleetJournalBuilder:
        self._events.append(
            RunCancelRequestedEvent(
                event_type=FleetRunEventType.RUN_CANCEL_REQUESTED,
                sequence=self._sequence(),
                fleet_run_id=self._fleet_run_id,
                generated_at=at,
            )
        )
        return self

    def run_completed(self, at: datetime) -> CanonicalFleetJournalBuilder:
        self._events.append(
            RunCompletedEvent(
                event_type=FleetRunEventType.RUN_COMPLETED,
                sequence=self._sequence(),
                fleet_run_id=self._fleet_run_id,
                generated_at=at,
            )
        )
        return self

    def run_cancelled(self, at: datetime) -> CanonicalFleetJournalBuilder:
        self._events.append(
            RunCancelledEvent(
                event_type=FleetRunEventType.RUN_CANCELLED,
                sequence=self._sequence(),
                fleet_run_id=self._fleet_run_id,
                generated_at=at,
            )
        )
        return self

    def run_failed(
        self, failure: FleetRunFailure, at: datetime
    ) -> CanonicalFleetJournalBuilder:
        self._events.append(
            RunFailedEvent(
                event_type=FleetRunEventType.RUN_FAILED,
                sequence=self._sequence(),
                fleet_run_id=self._fleet_run_id,
                failure=failure,
                generated_at=at,
            )
        )
        return self

    def build(self) -> tuple[FleetRunEvent, ...]:
        return tuple(self._events)
