from __future__ import annotations

from pydantic import Field

from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.models.capability import CapabilitySnapshot
from oh_goon.core.models.effect_origin import RepositoryEffectOrigin
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.target import OperationTarget


class RepositoryEffectIdentity(ImmutableModel):
    """Durable identity/provenance shared by recovery facts for one repository effect."""

    operation_id: str = Field(min_length=1)
    repository: RepositoryIdentity
    capability: CapabilitySnapshot
    target: OperationTarget | None = None
    origin: RepositoryEffectOrigin
