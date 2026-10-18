from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from oh_goon.core.models.fleet import FleetRepository, OperationState
from oh_goon.core.models.run import FleetRunDetail
from oh_goon.core.models.target import OperationTarget
from oh_goon.core.vocabulary import FleetScope, OperationAction


@dataclass(frozen=True, slots=True)
class FleetProjectionContext:
    """One indexed input cut reused by every FleetSnapshot sub-projection."""

    repositories: tuple[FleetRepository, ...]
    included: tuple[FleetRepository, ...]
    active_fleet_run: FleetRunDetail | None
    latest_fleet_runs: tuple[FleetRunDetail, ...]
    current_updates_by_repository: Mapping[str, OperationState]

    @classmethod
    def build(
        cls,
        repositories: list[FleetRepository] | tuple[FleetRepository, ...],
        active_fleet_run: FleetRunDetail | None,
        latest_fleet_runs: list[FleetRunDetail] | tuple[FleetRunDetail, ...],
    ) -> FleetProjectionContext:
        repository_cut = tuple(repositories)
        included = tuple(repo for repo in repository_cut if repo.scope is FleetScope.INCLUDED)
        latest_cut = tuple(latest_fleet_runs)
        updates = MappingProxyType(
            {
                repository.repository.repository_id: repository.update
                for repository in included
                if repository.update is not None
            }
        )
        return cls(
            repositories=repository_cut,
            included=included,
            active_fleet_run=active_fleet_run,
            latest_fleet_runs=latest_cut,
            current_updates_by_repository=updates,
        )

    def latest_run_exact(
        self,
        capability_id: str,
        target: OperationTarget | None = None,
    ) -> FleetRunDetail | None:
        matches = [
            detail
            for detail in self.latest_fleet_runs
            if detail.run.capability.capability_id == capability_id and detail.run.target == target
        ]
        return max(
            matches,
            key=lambda detail: (detail.run.accepted_at, detail.run.fleet_run_id),
            default=None,
        )

    def latest_run_family(self, action: OperationAction) -> FleetRunDetail | None:
        matches = [detail for detail in self.latest_fleet_runs if detail.run.action is action]
        return max(
            matches,
            key=lambda detail: (detail.run.accepted_at, detail.run.fleet_run_id),
            default=None,
        )

    def run_details(self) -> tuple[FleetRunDetail, ...]:
        if self.active_fleet_run is None or any(
            detail.run.fleet_run_id == self.active_fleet_run.run.fleet_run_id
            for detail in self.latest_fleet_runs
        ):
            return self.latest_fleet_runs
        return (*self.latest_fleet_runs, self.active_fleet_run)
