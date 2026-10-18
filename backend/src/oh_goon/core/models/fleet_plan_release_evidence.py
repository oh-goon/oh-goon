from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, computed_field, model_validator

from oh_goon.core.product_identity import stable_id
from oh_goon.core.models.base import ImmutableModel


class OciPublishReleaseEvidence(ImmutableModel):
    """Known immutable OCI publication that crossed the Fleet release boundary."""

    kind: Literal["OCI_PUBLISH"] = "OCI_PUBLISH"
    registry_authority: str = Field(min_length=3, max_length=253)
    repository: str = Field(min_length=1, max_length=220)
    manifest_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    published_reference: str = Field(min_length=1, max_length=512)
    tls_verified: Literal[True] = True
    digest_preserved: Literal[True] = True

    @model_validator(mode="after")
    def validate_reference(self) -> OciPublishReleaseEvidence:
        expected = f"{self.registry_authority}/{self.repository}@{self.manifest_digest}"
        if self.published_reference != expected:
            raise ValueError("OCI release evidence reference must match registry/repository/digest")
        return self

    @computed_field
    @property
    def release_id(self) -> str:
        return stable_id(
            "plan-release-effect",
            self.model_dump(mode="json", exclude_computed_fields=True),
        )


class KubernetesSetImageReleaseEvidence(ImmutableModel):
    """Known Kubernetes image state observed at the release boundary."""

    kind: Literal["KUBERNETES_SET_IMAGE"] = "KUBERNETES_SET_IMAGE"
    outcome: Literal["COMMITTED", "ALREADY_DESIRED"]
    api_authority: str = Field(min_length=3, max_length=253)
    namespace: str = Field(min_length=1, max_length=63)
    deployment: str = Field(min_length=1, max_length=253)
    container: str = Field(min_length=1, max_length=63)
    desired_image: str = Field(min_length=1, max_length=512)
    before_resource_version: str = Field(min_length=1, max_length=200)
    before_generation: int = Field(ge=0)
    after_resource_version: str = Field(min_length=1, max_length=200)
    after_generation: int = Field(ge=0)
    after_image: str = Field(min_length=1, max_length=512)
    tls_verified: Literal[True] = True
    resource_version_tested: Literal[True] = True
    current_image_tested: Literal[True] = True
    rollout_waited: Literal[False] = False

    @model_validator(mode="after")
    def validate_transition(self) -> KubernetesSetImageReleaseEvidence:
        if self.after_image != self.desired_image:
            raise ValueError("Kubernetes release evidence must observe the desired immutable image")
        if self.outcome == "ALREADY_DESIRED":
            if (
                self.before_resource_version != self.after_resource_version
                or self.before_generation != self.after_generation
            ):
                raise ValueError(
                    "ALREADY_DESIRED release evidence cannot claim a Deployment mutation"
                )
        else:
            if self.before_resource_version == self.after_resource_version:
                raise ValueError("COMMITTED release evidence requires a new resourceVersion")
            if self.after_generation <= self.before_generation:
                raise ValueError(
                    "COMMITTED release evidence requires an advanced Deployment generation"
                )
        return self

    @computed_field
    @property
    def release_id(self) -> str:
        return stable_id(
            "plan-release-effect",
            self.model_dump(mode="json", exclude_computed_fields=True),
        )


PlanReleaseEffectEvidence = Annotated[
    OciPublishReleaseEvidence | KubernetesSetImageReleaseEvidence,
    Field(discriminator="kind"),
]


class KubernetesRolloutVerificationOutcome(StrEnum):
    READY = "READY"
    TIMEOUT = "TIMEOUT"
    SUPERSEDED = "SUPERSEDED"
    PROGRESS_DEADLINE_EXCEEDED = "PROGRESS_DEADLINE_EXCEEDED"


class KubernetesRolloutVerificationEvidence(ImmutableModel):
    """Bounded observation of one exact Deployment generation after release."""

    kind: Literal["KUBERNETES_ROLLOUT_VERIFY"] = "KUBERNETES_ROLLOUT_VERIFY"
    outcome: KubernetesRolloutVerificationOutcome
    api_authority: str = Field(min_length=3, max_length=253)
    namespace: str = Field(min_length=1, max_length=63)
    deployment: str = Field(min_length=1, max_length=253)
    container: str = Field(min_length=1, max_length=63)
    expected_generation: int = Field(ge=1)
    expected_image: str = Field(min_length=1, max_length=512)
    observations: int = Field(ge=1)
    final_resource_version: str = Field(min_length=1, max_length=200)
    final_generation: int = Field(ge=1)
    final_image: str = Field(min_length=1, max_length=512)
    observed_generation: int | None = Field(default=None, ge=0)
    desired_replicas: int = Field(ge=0)
    replicas: int = Field(ge=0)
    updated_replicas: int = Field(ge=0)
    available_replicas: int = Field(ge=0)
    unavailable_replicas: int = Field(ge=0)
    progressing_status: Literal["True", "False", "Unknown"] | None = None
    progressing_reason: str | None = Field(default=None, max_length=120)
    timeout_seconds: float = Field(gt=0, le=3600)
    poll_interval_seconds: float = Field(ge=0.25, le=60)
    request_timeout_seconds: int = Field(ge=1, le=60)
    transport_retries: Literal[0] = 0
    tls_verified: Literal[True] = True

    @model_validator(mode="after")
    def validate_outcome(self) -> KubernetesRolloutVerificationEvidence:
        exact_target = (
            self.final_generation == self.expected_generation
            and self.final_image == self.expected_image
        )
        if self.outcome is KubernetesRolloutVerificationOutcome.READY:
            if (
                not exact_target
                or self.observed_generation is None
                or self.observed_generation < self.expected_generation
            ):
                raise ValueError(
                    "READY rollout evidence requires the exact observed Deployment generation"
                )
            if not (
                self.replicas == self.desired_replicas
                and self.updated_replicas == self.desired_replicas
                and self.available_replicas == self.desired_replicas
                and self.unavailable_replicas == 0
            ):
                raise ValueError(
                    "READY rollout evidence requires all desired replicas updated and available"
                )
        if self.outcome is KubernetesRolloutVerificationOutcome.SUPERSEDED and exact_target:
            raise ValueError("SUPERSEDED rollout evidence requires a different generation or image")
        if (
            self.outcome is KubernetesRolloutVerificationOutcome.PROGRESS_DEADLINE_EXCEEDED
            and self.progressing_reason != "ProgressDeadlineExceeded"
        ):
            raise ValueError("Progress-deadline evidence requires the canonical Kubernetes reason")
        return self

    @computed_field
    @property
    def verification_id(self) -> str:
        return stable_id(
            "plan-release-verification",
            self.model_dump(mode="json", exclude_computed_fields=True),
        )
