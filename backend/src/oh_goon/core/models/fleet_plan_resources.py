from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator

from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.models.git_sources import LocalApplicationSource, LockedExecutionSource


class CapturedProjectSource(ImmutableModel):
    """Captured package identity; executable source bytes travel privately."""

    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    archive_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    byte_count: int = Field(ge=0, le=64 * 1024 * 1024)
    entry_count: int = Field(ge=1, le=4096)
    application: LocalApplicationSource | LockedExecutionSource | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def exact_application(self) -> CapturedProjectSource:
        if (
            isinstance(self.application, LocalApplicationSource)
            and not Path(self.application.path).is_absolute()
        ):
            raise ValueError("Captured application source requires its resolved absolute root")
        if (
            isinstance(self.application, LockedExecutionSource)
            and self.application.kind != "application"
        ):
            raise ValueError("Captured application Git provenance must select an application")
        return self


class ExtensionDeclaration(ImmutableModel):
    name: str = Field(min_length=1, max_length=80)
    source: str = Field(min_length=1, max_length=500)
    revision: str = Field(pattern=r"^[0-9a-fA-F]{40}$")
    commands: tuple[str, ...] = Field(min_length=1, max_length=64)

    @field_validator("commands")
    @classmethod
    def validate_commands(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("Extension command allowlist must be unique")
        return value


class ExtensionCatalog(ImmutableModel):
    version: Literal[2] = 2
    extensions: tuple[ExtensionDeclaration, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def validate_names(self) -> ExtensionCatalog:
        names = [extension.name for extension in self.extensions]
        if len(names) != len(set(names)):
            raise ValueError("Fleet extension names must be unique")
        return self


class ManagedExtension(ImmutableModel):
    """One Fleet-owned checkout for an exact extension source revision."""

    extension_id: str
    source: str
    revision: str
    project_name: str
    project_path: Path
    project_fingerprint: str
    pyproject_sha256: str
    uv_lock_sha256: str
    scripts: tuple[str, ...] = Field(min_length=1)


class ManagedToolRuntime(ImmutableModel):
    """Fleet-owned prepared runtime for one pinned built-in plan tool."""

    runtime_id: str = Field(min_length=1)
    tool: Literal["pre-commit"]
    requirement: str = Field(min_length=1, max_length=120)
    pyproject_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    uv_lock_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
