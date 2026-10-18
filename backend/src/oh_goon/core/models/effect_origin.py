from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.vocabulary import EffectAttemptOwner


class DirectEffectOrigin(ImmutableModel):
    """One repository effect admitted outside a Fleet run."""

    kind: Literal[EffectAttemptOwner.DIRECT]


class FleetEffectOrigin(ImmutableModel):
    """One repository effect owned by a specific Fleet run."""

    kind: Literal[EffectAttemptOwner.FLEET]
    fleet_run_id: str = Field(min_length=1)


RepositoryEffectOrigin = Annotated[
    DirectEffectOrigin | FleetEffectOrigin,
    Field(discriminator="kind"),
]
