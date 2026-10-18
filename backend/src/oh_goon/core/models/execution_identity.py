from __future__ import annotations

from pydantic import Field

from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.models.goon_plan_source import FleetPlanSource


class ExecutionIdentity(ImmutableModel):
    """Verified semantic identity of one already-resolved execution definition."""

    digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class ResolvedExecutionReference(ImmutableModel):
    """Complete structural identity of one resolved and locked execution definition."""

    plan_id: str = Field(min_length=1)
    plan_name: str = Field(min_length=1)
    source: FleetPlanSource
    identity: ExecutionIdentity
