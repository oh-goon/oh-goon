from __future__ import annotations

from collections.abc import Iterable

from oh_goon.core.models.fleet_plan_evidence import (
    PlanImportApplicabilityEvidence,
    PlanSnapshotEvidence,
)
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.run_admission import PlanRepositoryAdmissionEvidence
from oh_goon.core.models.source_authority import RepositorySourceAuthority
from oh_goon.core.models.target import FleetPlanTarget, OperationTarget
from oh_goon.core.product_identity import stable_id


def source_authority_for(repository_id: str) -> RepositorySourceAuthority:
    """Build deterministic source authority for tests that already assume admission."""

    return RepositorySourceAuthority.create(
        subject_instance_id=stable_id(
            "repo-subject-instance",
            {"fixture": "already-admitted-read-projection", "repository_id": repository_id},
        ),
        source_snapshot=PlanSnapshotEvidence(
            sha256=stable_id("fixture-source", repository_id).removeprefix("fixture-source-"),
            byte_count=0,
            entry_count=0,
        ),
    )


def repository_admission_for(
    *,
    repository_id: str,
    plan_id: str,
    active_step_ids: tuple[str, ...] = (),
    import_applicability: tuple[PlanImportApplicabilityEvidence, ...] = (),
) -> PlanRepositoryAdmissionEvidence:
    return PlanRepositoryAdmissionEvidence.create(
        repository_id=repository_id,
        plan_id=plan_id,
        semantic_preview_id=stable_id(
            "fleet-plan-repository-preview",
            {
                "fixture": "already-admitted-read-projection",
                "repository_id": repository_id,
                "plan_id": plan_id,
            },
        ),
        import_applicability=import_applicability,
        active_step_ids=active_step_ids,
        source_authority=source_authority_for(repository_id),
    )


def repository_admissions_for(
    target: OperationTarget | None,
    repositories: Iterable[RepositoryIdentity],
) -> tuple[PlanRepositoryAdmissionEvidence, ...]:
    """Build structurally current Fleet Plan admission evidence for test projections.

    Tests that exercise the real admission owner should use its produced evidence instead.
    This helper exists only for tests constructing already-admitted read projections.
    """

    if not isinstance(target, FleetPlanTarget):
        return ()
    return tuple(
        repository_admission_for(
            repository_id=repository.repository_id,
            plan_id=target.plan.plan_id,
            active_step_ids=tuple(step.step_id for step in target.plan.steps),
        )
        for repository in sorted(repositories, key=lambda item: item.repository_id)
    )
