from __future__ import annotations

from pydantic import Field, model_validator

from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.models.git_sources import LockedGoonSource


class LockedGoon(ImmutableModel):
    goon: str = Field(min_length=1, max_length=240)
    digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class LockedExternalArtifact(ImmutableModel):
    path: str = Field(min_length=1, max_length=500)
    digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class LockedSkillSource(ImmutableModel):
    """One declared Git collection resolved by native Git to an exact commit."""

    name: str = Field(pattern=r"^[a-z][a-z0-9-]*$", max_length=64)
    source: str = Field(min_length=1, max_length=2000)
    ref: str = Field(min_length=1, max_length=240)
    commit: str = Field(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
    skills: tuple[str, ...] = Field(min_length=1, max_length=128)


class ExecutionLock(ImmutableModel):
    """Tiny generated fingerprint of one resolved Oh Goon execution identity."""

    lock_version: int
    plan: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    execution: tuple[LockedGoon, ...] = Field(default_factory=tuple)
    external: tuple[LockedExternalArtifact, ...] = Field(default_factory=tuple)
    skill_sources: tuple[LockedSkillSource, ...] = Field(default_factory=tuple, max_length=128)
    goon_sources: tuple[LockedGoonSource, ...] = Field(default=(), max_length=128)
    digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_lock_version(self) -> ExecutionLock:
        if self.lock_version != 1:
            raise ValueError(f"Unsupported goon.lock version: {self.lock_version}")
        return self

    @model_validator(mode="after")
    def validate_goon_source_pins(self) -> ExecutionLock:
        selections: set[tuple[str, str, str, str, str | None]] = set()
        commits: dict[tuple[str, str], str] = {}
        plans = 0
        for source in self.goon_sources:
            selection = (source.kind, source.git, source.ref, source.path, source.plan)
            if selection in selections:
                raise ValueError("goon.lock contains duplicate Goon source selections")
            selections.add(selection)
            repository = (source.git, source.ref)
            if repository in commits and commits[repository] != source.commit:
                raise ValueError("goon.lock Goon sources disagree on the selected commit")
            commits[repository] = source.commit
            plans += source.kind == "plan"
        if plans > 1:
            raise ValueError("goon.lock selects more than one root Git Plan")
        return self
