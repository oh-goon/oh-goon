from __future__ import annotations

import re
from typing import Literal

from pydantic import Field, field_validator, model_validator

from oh_goon.core.models.base import ImmutableModel

_OCI_HOSTNAME = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"
)


class OciRegistryEndpoint(ImmutableModel):
    """One registry endpoint whose pull authority is admitted before execution."""

    hostname: str = Field(min_length=3, max_length=253)
    port: int = Field(default=443, ge=1, le=65535)

    @field_validator("hostname")
    @classmethod
    def validate_hostname(cls, value: str) -> str:
        if value != value.lower() or value.endswith(".") or _OCI_HOSTNAME.fullmatch(value) is None:
            raise ValueError(
                "Fleet OCI registry hostname must be a canonical lowercase DNS name with a dot"
            )
        return value

    @property
    def image_authority(self) -> str:
        return self.hostname if self.port == 443 else f"{self.hostname}:{self.port}"


class AuthoringOciRegistryEndpoint(ImmutableModel):
    """Authoring form may contain a compile-time expression; resolved form never does."""

    hostname: str = Field(min_length=3, max_length=253)
    port: int = Field(default=443, ge=1, le=65535)

    @field_validator("hostname")
    @classmethod
    def validate_hostname(cls, value: str) -> str:
        if "${{" in value:
            return value
        OciRegistryEndpoint(hostname=value, port=443)
        return value


class AuthoringOciBuildPolicy(ImmutableModel):
    platform: str = Field(default="linux/amd64", min_length=1, max_length=40)
    pull: Literal["always"] = "always"
    run_network: Literal["none"] = "none"
    tls_verify: Literal[True] = True
    registry_retries: Literal[0] = 0
    source_date_epoch: int = Field(default=0, ge=0, le=253402300799)

    @field_validator("platform")
    @classmethod
    def validate_platform(cls, value: str) -> str:
        if "${{" in value:
            return value
        if value not in {"linux/amd64", "linux/arm64"}:
            raise ValueError("Fleet OCI platform must be linux/amd64 or linux/arm64")
        return value


class OciBuildPolicy(ImmutableModel):
    """Fixed OCI build semantics; no free-form builder flags are part of the plan contract."""

    platform: Literal["linux/amd64", "linux/arm64"] = "linux/amd64"
    pull: Literal["always"] = "always"
    run_network: Literal["none"] = "none"
    tls_verify: Literal[True] = True
    registry_retries: Literal[0] = 0
    source_date_epoch: int = Field(default=0, ge=0, le=253402300799)


class OciArchiveOutput(ImmutableModel):
    """One OCI archive plus deterministic metadata emitted by the typed builder."""

    archive: str = Field(default="oci/image.oci.tar", min_length=1, max_length=240)
    metadata: str = Field(default="oci/image-build.json", min_length=1, max_length=240)
    retention: Literal["evidence", "content_store"] = "content_store"

    @model_validator(mode="after")
    def validate_distinct_paths(self) -> OciArchiveOutput:
        if self.archive == self.metadata:
            raise ValueError("Fleet OCI archive and metadata paths must be distinct")
        return self
