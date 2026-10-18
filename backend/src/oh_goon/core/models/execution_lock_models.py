from __future__ import annotations

from pydantic import Field, model_validator

from oh_goon.core.models.base import ImmutableModel


class LockedGoon(ImmutableModel):
    goon: str = Field(min_length=1, max_length=240)
    digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class LockedExternalArtifact(ImmutableModel):
    path: str = Field(min_length=1, max_length=500)
    digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class ExecutionLock(ImmutableModel):
    """Tiny generated fingerprint of one resolved Oh Goon execution identity."""

    lock_version: int
    plan: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    execution: tuple[LockedGoon, ...] = Field(default_factory=tuple)
    external: tuple[LockedExternalArtifact, ...] = Field(default_factory=tuple)
    digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_lock_version(self) -> ExecutionLock:
        if self.lock_version != 1:
            raise ValueError(f"Unsupported goon.lock version: {self.lock_version}")
        return self
