from pydantic import BaseModel, Field, model_validator

from oh_goon.core.capability_registry import capability_for
from oh_goon.core.models.run_admission import requested_fleet_repository_scope
from oh_goon.core.models.scheduling import SchedulingPriority
from oh_goon.core.models.target import FleetPlanTarget, OperationTarget
from oh_goon.core.vocabulary import OperationAction


class FleetRunRequest(BaseModel):
    fleet_run_id: str = Field(min_length=1)
    action: OperationAction
    target: OperationTarget | None = None
    repository_ids: list[str] | None = Field(default=None, min_length=1)
    repository_instance_ids: dict[str, str] | None = None
    all_repositories: bool = False
    requested_priority: SchedulingPriority = SchedulingPriority.NORMAL

    @model_validator(mode="after")
    def validate_target_action(self) -> "FleetRunRequest":
        if self.action is OperationAction.EXECUTE_PLAN or isinstance(self.target, FleetPlanTarget):
            raise ValueError(
                "Fleet Plan execution must use the backend-resolved Team or Shared Plan boundary"
            )
        capability_for(self.action, self.target)
        if self.repository_ids is not None and len(self.repository_ids) != len(
            set(self.repository_ids)
        ):
            raise ValueError("repository_ids must be unique")
        if self.repository_ids is not None:
            if self.repository_instance_ids is None or set(self.repository_instance_ids) != set(
                self.repository_ids
            ):
                raise ValueError("repository_instance_ids must exactly match repository_ids")
        elif self.repository_instance_ids is not None:
            raise ValueError("repository_instance_ids requires explicit repository_ids")
        requested_fleet_repository_scope(
            self.target,
            repository_ids=set(self.repository_ids) if self.repository_ids is not None else None,
            all_repositories=self.all_repositories,
        )
        return self


class FleetRunChildRequest(BaseModel):
    """Identity for a fresh Run derived from backend-owned durable Run authority."""

    new_fleet_run_id: str = Field(min_length=1)
