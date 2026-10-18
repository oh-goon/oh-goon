from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import TypeVar

from oh_goon.core.models.operation import OperationReport
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.run import FleetRunFailure, FleetRunState
from oh_goon.core.models.run_admission import FleetRepositoryScope, FleetRunAdmissionIntent
from oh_goon.core.product_identity import repository_attempt_operation_id
from oh_goon.core.vocabulary import FleetRunEventType, FleetRunTerminalIntent
from oh_goon.runtime.journal import Journal

T = TypeVar("T")


def _write(journal: Journal, mutation: Callable[[sqlite3.Connection], T]) -> T:
    """Test-only transaction driver for direct Journal unit setup."""
    with journal._database.transaction(write=True) as connection:
        return mutation(connection)


def commit_operation(journal: Journal, report: OperationReport) -> None:
    _write(journal, lambda connection: journal.commit_operation_in(connection, report))


def admit_run(
    journal: Journal,
    fleet_run_id: str,
    intent: FleetRunAdmissionIntent,
    repository_scope: FleetRepositoryScope,
    targets: list[RepositoryIdentity],
) -> FleetRunState:
    return _write(
        journal,
        lambda connection: journal.admit_run_in(
            connection, fleet_run_id, intent, repository_scope, targets
        ),
    )


def start_run(journal: Journal, fleet_run_id: str) -> FleetRunState:
    return _write(journal, lambda connection: journal.start_run_in(connection, fleet_run_id))


def start_repository(
    journal: Journal, fleet_run_id: str, repository: RepositoryIdentity
) -> str:
    operation_id = repository_attempt_operation_id(fleet_run_id, repository.repository_id)
    journal.record_execution_lifecycle(
        fleet_run_id=fleet_run_id,
        repository_id=repository.repository_id,
        operation_id=operation_id,
        event_type=FleetRunEventType.REPOSITORY_STARTED,
    )
    return operation_id


def terminalize_run(
    journal: Journal,
    fleet_run_id: str,
    intent: FleetRunTerminalIntent,
    failure: FleetRunFailure | None = None,
) -> FleetRunState:
    return _write(
        journal,
        lambda connection: journal.terminalize_run_in(connection, fleet_run_id, intent, failure),
    )
