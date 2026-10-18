from __future__ import annotations

from oh_goon.core.capability_registry import recipe_capability_definitions
from oh_goon.core.product_identity import repository_subject_instance_id
from oh_goon.core.models.command_center import FleetCommandCenter, RecipeFleetStatus
from oh_goon.core.models.run import fleet_run_outcome
from oh_goon.core.models.target import UvRecipeTarget
from oh_goon.core.recipes import recipe_spec
from oh_goon.runtime.state import LocalState
from oh_goon.services.repositories import RepositoryService


class CommandCenterService:
    """Project the supported UV command surface over one pinned durable state cut."""

    def __init__(self, repositories: RepositoryService, state: LocalState) -> None:
        self._repositories = repositories
        self._state = state

    def status(self) -> FleetCommandCenter:
        state = self._state.read_view()
        included = self._repositories.included(state)
        rows: list[RecipeFleetStatus] = []
        for capability in recipe_capability_definitions(configurable=False):
            recipe = capability.recipe
            assert recipe is not None
            spec = recipe_spec(recipe)
            target = UvRecipeTarget(kind="recipe", recipe=recipe)
            successful = not_run = 0
            attention_ids: list[str] = []
            stale_ids: list[str] = []
            for entry in included:
                current = state.journal.current_capability(
                    entry.repository.path, capability.capability_id, target
                )
                if current is None:
                    not_run += 1
                elif current.is_stale:
                    stale_ids.append(entry.repository.repository_id)
                elif current.current_needs_attention:
                    attention_ids.append(entry.repository.repository_id)
                elif current.current_successful:
                    successful += 1
            latest = state.journal.latest_run_exact(capability.capability_id, target)
            rows.append(
                RecipeFleetStatus(
                    spec=spec,
                    successful=successful,
                    not_run=not_run,
                    attention_repository_ids=sorted(attention_ids),
                    stale_repository_ids=sorted(stale_ids),
                    latest_run_id=latest.run.fleet_run_id if latest else None,
                    latest_outcome=fleet_run_outcome(latest.run) if latest else None,
                )
            )
        return FleetCommandCenter(
            durable_revision=state.durable_revision,
            journal_revision=state.journal.revision,
            repository_ids=tuple(sorted(entry.repository.repository_id for entry in included)),
            repository_instance_ids={
                entry.repository.repository_id: repository_subject_instance_id(entry.repository.path)
                for entry in included
            },
            recipes=rows,
        )
