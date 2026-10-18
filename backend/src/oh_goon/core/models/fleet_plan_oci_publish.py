from __future__ import annotations

import re
from typing import Literal

from pydantic import Field, field_validator, model_validator

from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.models.fleet_plan_oci import (
    AuthoringOciRegistryEndpoint,
    OciRegistryEndpoint,
)

_REPOSITORY = re.compile(
    r"^[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*(?:/[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*)*$"
)
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
OCI_PUBLISH_OUTPUT_NAMES = ("OCI_DIGEST", "OCI_REFERENCE")


class AuthoringOciPublishTarget(ImmutableModel):
    registry: AuthoringOciRegistryEndpoint
    repository: str = Field(min_length=1, max_length=220)

    @field_validator("repository")
    @classmethod
    def validate_repository(cls, value: str) -> str:
        if "${{" in value:
            return value
        if _REPOSITORY.fullmatch(value) is None:
            raise ValueError(
                "Fleet OCI publish repository must use canonical lowercase OCI name components"
            )
        return value


class OciPublishTarget(ImmutableModel):
    registry: OciRegistryEndpoint
    repository: str = Field(min_length=1, max_length=220, pattern=_REPOSITORY.pattern)

    @property
    def repository_reference(self) -> str:
        return f"{self.registry.image_authority}/{self.repository}"

    def digest_reference(self, digest: str) -> str:
        if _DIGEST.fullmatch(digest) is None:
            raise ValueError("Fleet OCI publish digest must be canonical sha256:<64 lowercase hex>")
        return f"{self.repository_reference}@{digest}"


class OciPublishPolicy(ImmutableModel):
    """Fixed publisher semantics. Mutable tag creation is intentionally not part of publish."""

    reference_mode: Literal["digest"] = "digest"
    preserve_digests: Literal[True] = True
    tls_verify: Literal[True] = True
    registry_retries: Literal[0] = 0
    parallel_copies: Literal[1] = 1


class OciPublishOutput(ImmutableModel):
    metadata: str = Field(default="oci/image-publish.json", min_length=1, max_length=240)
    digest: str = Field(default="oci/image-digest.txt", min_length=1, max_length=240)
    reference: str = Field(default="oci/image-reference.txt", min_length=1, max_length=240)

    @model_validator(mode="after")
    def validate_distinct_paths(self) -> OciPublishOutput:
        values = (self.metadata, self.digest, self.reference)
        if len(values) != len(set(values)):
            raise ValueError("Fleet OCI publish output paths must be distinct")
        return self
