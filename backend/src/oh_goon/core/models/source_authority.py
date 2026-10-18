from __future__ import annotations

from pydantic import Field, model_validator

from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.models.fleet_plan_evidence import PlanSnapshotEvidence
from oh_goon.core.product_identity import stable_id


class RepositorySourceAuthority(ImmutableModel):
    """Exact repository subject and filtered source bytes admitted for execution."""

    subject_instance_id: str = Field(pattern=r"^repo-subject-instance-[0-9a-f]{64}$")
    source_snapshot: PlanSnapshotEvidence
    authority_id: str = Field(pattern=r"^repository-source-authority-[0-9a-f]{64}$")

    @classmethod
    def create(
        cls,
        *,
        subject_instance_id: str,
        source_snapshot: PlanSnapshotEvidence,
    ) -> RepositorySourceAuthority:
        authority_id = stable_id(
            "repository-source-authority",
            {
                "subject_instance_id": subject_instance_id,
                "source_snapshot": source_snapshot.model_dump(mode="json"),
            },
        )
        return cls(
            subject_instance_id=subject_instance_id,
            source_snapshot=source_snapshot,
            authority_id=authority_id,
        )

    @model_validator(mode="after")
    def validate_authority_id(self) -> RepositorySourceAuthority:
        expected = stable_id(
            "repository-source-authority",
            {
                "subject_instance_id": self.subject_instance_id,
                "source_snapshot": self.source_snapshot.model_dump(mode="json"),
            },
        )
        if self.authority_id != expected:
            raise ValueError("Repository source authority_id must match its admitted facts")
        return self
