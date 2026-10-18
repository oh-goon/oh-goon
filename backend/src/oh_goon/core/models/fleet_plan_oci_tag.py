from __future__ import annotations

import re

from pydantic import Field, field_validator, model_validator

from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.models.fleet_plan_oci import (
    AuthoringOciRegistryEndpoint,
    OciRegistryEndpoint,
)

_REPOSITORY = re.compile(
    r"^[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*(?:/[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*)*$"
)
_TAG = re.compile(r"^[a-z0-9_][a-z0-9_.-]{0,127}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
OCI_TAG_ASSERT_OUTPUT_NAMES = ("OCI_TAG_DIGEST", "OCI_TAG_RESOLVED_REFERENCE")
_STEP_OUTPUT = re.compile(
    r"^\$\{\{\s*steps\.([A-Za-z0-9_.\-\[\]=]+)\.outputs\.([A-Za-z_][A-Za-z0-9_-]{0,63})\s*\}\}$"
)


class AuthoringOciTagTarget(ImmutableModel):
    """Static mutable registry name inspected by a read-only tag assertion."""

    registry: AuthoringOciRegistryEndpoint
    repository: str = Field(min_length=1, max_length=220)
    tag: str = Field(min_length=1, max_length=128)

    @field_validator("repository")
    @classmethod
    def validate_repository(cls, value: str) -> str:
        if "${{" in value:
            return value
        if _REPOSITORY.fullmatch(value) is None:
            raise ValueError(
                "Fleet OCI tag repository must use canonical lowercase OCI name components"
            )
        return value

    @field_validator("tag")
    @classmethod
    def validate_tag(cls, value: str) -> str:
        if "${{" in value:
            return value
        if _TAG.fullmatch(value) is None:
            raise ValueError(
                "Fleet OCI tag must be lowercase and use only letters, digits, underscore, dot, or dash"
            )
        return value


class OciTagTarget(ImmutableModel):
    registry: OciRegistryEndpoint
    repository: str = Field(min_length=1, max_length=220, pattern=_REPOSITORY.pattern)
    tag: str = Field(min_length=1, max_length=128, pattern=_TAG.pattern)

    @property
    def repository_reference(self) -> str:
        return f"{self.registry.image_authority}/{self.repository}"

    @property
    def tag_reference(self) -> str:
        return f"{self.repository_reference}:{self.tag}"

    def digest_reference(self, digest: str) -> str:
        if _DIGEST.fullmatch(digest) is None:
            raise ValueError("Fleet OCI tag digest must be canonical sha256:<64 lowercase hex>")
        return f"{self.repository_reference}@{digest}"


class OciTagAssertPolicy(ImmutableModel):
    """Read-only remote observation policy; no hidden Skopeo retries or registry config."""

    tls_verify: bool = True
    registry_retries: int = Field(default=0, ge=0, le=0)

    @model_validator(mode="after")
    def validate_strict_policy(self) -> OciTagAssertPolicy:
        if self.tls_verify is not True:
            raise ValueError("Fleet OCI tag assertion requires TLS verification")
        return self


class OciTagAssertOutput(ImmutableModel):
    metadata: str = Field(default="oci/tag-assert.json", min_length=1, max_length=240)
    digest: str = Field(default="oci/tag-digest.txt", min_length=1, max_length=240)
    resolved_reference: str = Field(
        default="oci/tag-resolved-reference.txt", min_length=1, max_length=240
    )

    @model_validator(mode="after")
    def validate_distinct_paths(self) -> OciTagAssertOutput:
        values = (self.metadata, self.digest, self.resolved_reference)
        if len(values) != len(set(values)):
            raise ValueError("Fleet OCI tag assertion output paths must be distinct")
        return self


def is_valid_oci_expected_digest(value: str) -> bool:
    """Return whether one value is a canonical digest or exact earlier-step output reference."""

    return _DIGEST.fullmatch(value) is not None or _STEP_OUTPUT.fullmatch(value) is not None


def validate_oci_expected_digest(value: str) -> str:
    """Pydantic validator for one canonical digest or exact earlier-step output reference."""

    if is_valid_oci_expected_digest(value):
        return value
    raise ValueError(
        "Fleet OCI expected digest must be sha256:<64 lowercase hex> or one exact earlier step output reference"
    )
