from __future__ import annotations

from pydantic import Field

from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.target import OperationTarget


class ReviewPreparation(ImmutableModel):
    update_fleet_run_id: str
    target: OperationTarget | None = None
    repositories: tuple[RepositoryIdentity, ...] = Field(default_factory=tuple)
