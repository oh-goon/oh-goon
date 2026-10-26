from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from oh_goon.core.capability_registry import capability_for
from oh_goon.core.models.agent_repair import (
    AgentCampaignIntent,
    AgentCampaignRecord,
    AgentCampaignUnstartedSelection,
    AgentRepairBudget,
    AgentRepairStopReason,
)
from oh_goon.core.models.repository import RepositoryIdentity
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


class AgentCampaignInvocationResult(BaseModel):
    """Observed pre-intent invocation outcome, without Journal decision authority."""

    stop_reason: AgentRepairStopReason
    budget: AgentRepairBudget
    message: str
    authority: Literal["invocation-observation;not-a-committed-controller-decision"] = (
        "invocation-observation;not-a-committed-controller-decision"
    )


class AgentCampaignResult(BaseModel):
    """HTTP transport of the existing canonical campaign projection."""

    schema_name: Literal["oh-goon.agent-campaign-result/v2"] = Field(alias="schema")
    campaign_id: str
    journal_revision: int | None
    invocation_result: AgentCampaignInvocationResult | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    result: Literal["converged", "test_passed", "failed", "incomplete"]
    incomplete_reasons: tuple[str, ...]
    controller_result: dict[str, Any]
    intent: AgentCampaignIntent | None
    complete: bool
    safe_next_action: str
    unstarted_selection: AgentCampaignUnstartedSelection | None
    decision_history: tuple[AgentCampaignRecord, ...]
    original_execution_digest: str | None
    repositories: tuple[RepositoryIdentity, ...]
    initial_candidate: str | None
    last_admitted_candidate: str | None
    final_candidate: str | None
    runs: tuple[dict[str, Any], ...]
    reviews: tuple[dict[str, Any], ...]
    findings: tuple[dict[str, Any], ...]
    adjudications: tuple[dict[str, Any], ...]
    repair_attempts: tuple[dict[str, Any], ...]
    verification: tuple[dict[str, Any], ...]
    claim_authority: str
    continuation_authority: str
