from __future__ import annotations

from pydantic import Field

from oh_goon.core.models.base import ImmutableModel


class FleetPlanSourceDocument(ImmutableModel):
    """One observed Plan YAML source revision used by import/source integrations."""

    root: str = Field(min_length=1)
    path: str = Field(min_length=1)
    revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    content: str
