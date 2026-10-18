from __future__ import annotations

from enum import StrEnum

from pydantic import AwareDatetime, Field

from oh_goon.core.models.base import ImmutableModel


class SchedulingPriority(StrEnum):
    """Canonical workload priority. Provider-specific values never reach scheduling."""

    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    NORMAL = "NORMAL"
    LOW = "LOW"
    BACKGROUND = "BACKGROUND"


class PriorityScope(StrEnum):
    DOMAIN = "DOMAIN"
    SCHEDULING_SCOPE = "SCHEDULING_SCOPE"


class SchedulingEnvelope(ImmutableModel):
    """Durable scheduling identity projected by the Scheduling Authority."""

    scheduling_scope_id: str = Field(default="local-default", min_length=1)
    admission_domain_id: str = Field(default="default", min_length=1)
    run_id: str = Field(min_length=1)
    requested_priority: SchedulingPriority = SchedulingPriority.NORMAL
    admitted_priority: SchedulingPriority = SchedulingPriority.NORMAL
    priority_scope: PriorityScope = PriorityScope.DOMAIN
    priority_decision_reason: str = Field(default="DEFAULT_OR_AUTHORIZED", min_length=1)
    queue_seq: int = Field(ge=1)
    scheduling_revision: int = Field(default=0, ge=0)
    scheduling_policy_revision: str = Field(default="local-v1", min_length=1)
    priority_policy_revision: str = Field(default="local-v1", min_length=1)
    created_at: AwareDatetime
    admitted_at: AwareDatetime | None = None
