from __future__ import annotations

from oh_goon.core.models.fleet import FleetSnapshot
from oh_goon.core.projections import build_fleet_snapshot
from oh_goon.runtime.state import LocalState
from oh_goon.services.fleet_projection import repository_view
from oh_goon.services.repositories import RepositoryService


class FleetService:
    """Read-only Fleet snapshot projection over one durable state cut."""

    def __init__(self, repositories: RepositoryService, state: LocalState) -> None:
        self._repositories = repositories
        self._state = state

    def snapshot(self) -> FleetSnapshot:
        state = self._state.read_view()
        repositories = [
            repository_view(entry, state.journal) for entry in self._repositories.list(state)
        ]
        return build_fleet_snapshot(
            repositories,
            durable_revision=state.durable_revision,
            journal_revision=state.journal.revision,
            active_fleet_run=state.journal.active_run_detail(),
            latest_fleet_runs=state.journal.latest_run_details(),
        )
