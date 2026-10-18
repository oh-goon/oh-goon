from __future__ import annotations

from pydantic import Field, model_validator

from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.models.repository import RepositoryIdentity


class FleetFailureInventory(ImmutableModel):
    """Bounded summary of current repository failures derived from execution truth."""

    journal_revision: int = Field(ge=0)
    failing_repositories: int = Field(ge=0)
    failure_groups: int = Field(ge=0)


class FleetFailureGroup(ImmutableModel):
    group_id: str = Field(min_length=1)
    plan_id: str = Field(min_length=1)
    plan_name: str = Field(min_length=1)
    source_kind: str = Field(min_length=1)
    goon_name: str | None = Field(default=None, min_length=1, max_length=160)
    step_id: str | None = Field(default=None, min_length=1, max_length=80)
    affected_repositories: int = Field(ge=1)
    signature_count: int = Field(ge=0)


class FleetFailureGroupPage(ImmutableModel):
    journal_revision: int = Field(ge=0)
    total: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    items: tuple[FleetFailureGroup, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def validate_page(self) -> "FleetFailureGroupPage":
        if len(self.items) > self.limit:
            raise ValueError("Failure group page cannot exceed its requested limit")
        return self


class FleetFailureRepositoryItem(ImmutableModel):
    repository: RepositoryIdentity
    fleet_run_id: str = Field(min_length=1)
    group_id: str = Field(min_length=1)
    plan_id: str = Field(min_length=1)
    plan_name: str = Field(min_length=1)
    goon_name: str | None = Field(default=None, min_length=1, max_length=160)
    step_id: str | None = Field(default=None, min_length=1, max_length=80)
    signature_id: str | None = Field(default=None, min_length=1)
    failure_code: str | None = Field(default=None, min_length=1, max_length=120)
    failure_message: str | None = Field(default=None, min_length=1, max_length=500)


class FleetFailureRepositoryPage(ImmutableModel):
    journal_revision: int = Field(ge=0)
    group: FleetFailureGroup
    total: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    items: tuple[FleetFailureRepositoryItem, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def validate_page(self) -> "FleetFailureRepositoryPage":
        if len(self.items) > self.limit:
            raise ValueError("Failure repository page cannot exceed its requested limit")
        if any(item.group_id != self.group.group_id for item in self.items):
            raise ValueError("Failure repository items must belong to the selected group")
        return self
