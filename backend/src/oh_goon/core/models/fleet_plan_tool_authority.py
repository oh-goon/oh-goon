from __future__ import annotations

from typing import Literal

from pydantic import Field, computed_field

from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.product_identity import stable_id


class PlanHostToolAuthorityEvidence(ImmutableModel):
    """Exact executable bytes frozen from the admitted execution environment."""

    name: str = Field(min_length=1, max_length=240)
    origin: Literal["host"] = "host"
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    byte_count: int = Field(ge=0)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def authority_id(self) -> str:
        return stable_id(
            "plan-host-tool-authority",
            {
                "name": self.name,
                "sha256": self.sha256,
                "byte_count": self.byte_count,
            },
        )
