from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, computed_field

from oh_goon.core.models.background import BackgroundFailure
from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.models.runtime_safety import RuntimeSafetyProof
from oh_goon.core.vocabulary import SystemHealthStatus, ToolHealthReason, ToolStatus


class _ToolHealthBase(ImmutableModel):
    """One concrete tool observation; status determines which companion evidence is valid."""

    name: str = Field(min_length=1)
    status: ToolStatus
    required: bool

    @computed_field  # type: ignore[prop-decorator]
    @property
    def reason_code(self) -> ToolHealthReason:
        return {
            ToolStatus.READY: ToolHealthReason.NONE,
            ToolStatus.MISSING: ToolHealthReason.TOOL_MISSING,
            ToolStatus.BROKEN: ToolHealthReason.TOOL_BROKEN,
            ToolStatus.OPTIONAL_MISSING: ToolHealthReason.OPTIONAL_TOOL_MISSING,
        }[self.status]


class ReadyToolHealth(_ToolHealthBase):
    status: Literal[ToolStatus.READY]
    required: bool
    version: str | None = None
    detail: Literal[None] = None


class MissingRequiredToolHealth(_ToolHealthBase):
    status: Literal[ToolStatus.MISSING]
    required: Literal[True] = True
    version: Literal[None] = None
    detail: Literal[None] = None


class MissingOptionalToolHealth(_ToolHealthBase):
    status: Literal[ToolStatus.OPTIONAL_MISSING]
    required: Literal[False] = False
    version: Literal[None] = None
    detail: Literal[None] = None


class BrokenToolHealth(_ToolHealthBase):
    status: Literal[ToolStatus.BROKEN]
    required: bool
    version: Literal[None] = None
    detail: str | None = None


type ToolHealth = Annotated[
    ReadyToolHealth | MissingRequiredToolHealth | MissingOptionalToolHealth | BrokenToolHealth,
    Field(discriminator="status"),
]


class SystemHealth(ImmutableModel):
    tools: tuple[ToolHealth, ...]
    runtime_safety: RuntimeSafetyProof
    background_failures: tuple[BackgroundFailure, ...]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def status(self) -> SystemHealthStatus:
        if any(tool.required and tool.status is not ToolStatus.READY for tool in self.tools):
            return SystemHealthStatus.SETUP_REQUIRED
        if self.runtime_safety.effects_allowed and not self.background_failures:
            return SystemHealthStatus.READY
        return SystemHealthStatus.DEGRADED
