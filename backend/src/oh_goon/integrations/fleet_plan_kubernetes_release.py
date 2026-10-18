from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from oh_goon.core.models.fleet_plan import ResolvedKubernetesSetImageStep
from oh_goon.core.models.fleet_plan_release_evidence import KubernetesSetImageReleaseEvidence


@dataclass(frozen=True)
class KubernetesDeploymentObservation:
    resource_version: str
    generation: int
    container_index: int
    image: str


def kubernetes_release_evidence(
    step: ResolvedKubernetesSetImageStep,
    *,
    before: KubernetesDeploymentObservation,
    after: KubernetesDeploymentObservation,
    outcome: Literal["COMMITTED", "ALREADY_DESIRED"],
) -> KubernetesSetImageReleaseEvidence:
    """Build the bounded canonical release fact from authoritative API observations."""
    return KubernetesSetImageReleaseEvidence(
        outcome=outcome,
        api_authority=step.target.api.authority,
        namespace=step.target.namespace,
        deployment=step.target.name,
        container=step.target.container,
        desired_image=step.image,
        before_resource_version=before.resource_version,
        before_generation=before.generation,
        after_resource_version=after.resource_version,
        after_generation=after.generation,
        after_image=after.image,
    )
