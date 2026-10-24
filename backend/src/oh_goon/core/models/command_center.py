from __future__ import annotations

from pydantic import Field, computed_field, model_validator

from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.models.recipe import UvRecipeSpec
from oh_goon.core.vocabulary import FleetRunOutcome


class RecipeFleetStatus(ImmutableModel):
    spec: UvRecipeSpec
    successful: int = Field(ge=0)
    not_run: int = Field(ge=0)
    attention_repository_ids: tuple[str, ...] = Field(default_factory=tuple)
    stale_repository_ids: tuple[str, ...] = Field(default_factory=tuple)
    latest_run_id: str | None = None
    latest_outcome: FleetRunOutcome | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def needs_attention(self) -> int:
        return len(self.attention_repository_ids)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def stale(self) -> int:
        return len(self.stale_repository_ids)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def repositories(self) -> int:
        return self.successful + self.needs_attention + self.stale + self.not_run

    @model_validator(mode="after")
    def validate_latest_run(self) -> RecipeFleetStatus:
        if (self.latest_run_id is None) != (self.latest_outcome is None):
            raise ValueError("Recipe latest run ID and outcome must appear together")
        return self


class FleetCommandCenter(ImmutableModel):
    durable_revision: int = Field(ge=0)
    journal_revision: int = Field(ge=0)
    repository_ids: tuple[str, ...]
    repository_instance_ids: dict[str, str]
    recipes: tuple[RecipeFleetStatus, ...]

    @model_validator(mode="after")
    def validate_repository_scope(self) -> "FleetCommandCenter":
        if len(self.repository_ids) != len(set(self.repository_ids)):
            raise ValueError("Command Center repository IDs must be unique")
        if set(self.repository_instance_ids) != set(self.repository_ids):
            raise ValueError("Command Center repository instance IDs must match repository IDs")
        expected = len(self.repository_ids)
        if any(row.repositories != expected for row in self.recipes):
            raise ValueError("Command Center recipe counts must match its repository scope")
        return self
