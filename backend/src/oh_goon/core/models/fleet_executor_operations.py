from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, computed_field

from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.models.scheduling import PriorityScope, SchedulingPriority


class ExecutorAdmissionMode(StrEnum):
    """Operator intent for new placement on one logical executor identity."""

    ACTIVE = "active"
    DRAINING = "draining"


class ExecutorTrustState(StrEnum):
    """Operator trust decision applied independently from executor generation."""

    TRUSTED = "trusted"
    REVOKED = "revoked"


class FleetExecutorOperationalState(ImmutableModel):
    executor_id: str = Field(min_length=1, max_length=120)
    admission_mode: ExecutorAdmissionMode = ExecutorAdmissionMode.ACTIVE
    trust_state: ExecutorTrustState = ExecutorTrustState.TRUSTED
    revision: int = Field(default=0, ge=0)
    trust_epoch: int = Field(default=0, ge=0)


class FleetExecutorStatus(ImmutableModel):
    executor_id: str = Field(min_length=1, max_length=120)
    generation: int = Field(ge=1)
    priority: int = Field(ge=0)
    backend_id: str = Field(min_length=1)
    admission_mode: ExecutorAdmissionMode
    trust_state: ExecutorTrustState
    active_fleet_run_ids: tuple[str, ...] = ()
    operational_revision: int = Field(ge=0)

    @computed_field
    @property
    def accepting_new_runs(self) -> bool:
        return (
            self.admission_mode is ExecutorAdmissionMode.ACTIVE
            and self.trust_state is ExecutorTrustState.TRUSTED
        )


class FleetExecutorPoolSnapshot(ImmutableModel):
    executors: tuple[FleetExecutorStatus, ...]


class _PlacementQueueEntryBase(ImmutableModel):
    fleet_run_id: str = Field(min_length=1)
    sequence: int = Field(ge=1)
    position: int = Field(ge=1)
    scheduling_scope_id: str = Field(default="local-default", min_length=1)
    admission_domain_id: str = Field(default="default", min_length=1)
    requested_priority: SchedulingPriority = SchedulingPriority.NORMAL
    admitted_priority: SchedulingPriority = SchedulingPriority.NORMAL
    priority_scope: PriorityScope = PriorityScope.DOMAIN
    priority_decision_reason: str = Field(min_length=1)
    scheduling_revision: int = Field(default=0, ge=0)
    scheduling_policy_revision: str = Field(min_length=1)
    priority_policy_revision: str = Field(min_length=1)


class WaitingPlacementQueueEntry(_PlacementQueueEntryBase):
    state: Literal["waiting"]


class CancellationRequestedPlacementQueueEntry(_PlacementQueueEntryBase):
    state: Literal["cancellation_requested"]


PlacementQueueEntry = Annotated[
    WaitingPlacementQueueEntry | CancellationRequestedPlacementQueueEntry,
    Field(discriminator="state"),
]


class PlacementQueueSnapshot(ImmutableModel):
    entries: tuple[PlacementQueueEntry, ...]
