from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from oh_goon.core.models.base import ImmutableModel

PlanScalar = str | bool | int | None


class HostEnvSource(ImmutableModel):
    kind: Literal["host"] = "host"


class LiteralEnvSource(ImmutableModel):
    kind: Literal["literal"] = "literal"
    value: str | bool | int


PlanEnvSource = Annotated[
    HostEnvSource | LiteralEnvSource,
    Field(discriminator="kind"),
]


class PlanEnvDefinition(ImmutableModel):
    """One immutable named pipeline environment value.

    Definitions are pipeline data, not ambient process environment. They reach a
    subprocess only through an explicit step import.
    """

    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
    source: PlanEnvSource



class PlanStepImports(ImmutableModel):
    """Names explicitly exported into one fresh step process environment."""

    env: tuple[str, ...] = Field(default_factory=tuple, max_length=32)

    @model_validator(mode="after")
    def validate_names(self) -> PlanStepImports:
        if len(self.env) != len(set(self.env)):
            raise ValueError("Fleet step imports.env names must be unique")
        if any(not value or "=" in value or "\x00" in value for value in self.env):
            raise ValueError("Fleet step imports.env contains an invalid name")
        return self


class LiteralEnvironmentPart(ImmutableModel):
    kind: Literal["literal"] = "literal"
    value: str


class HostEnvironmentPart(ImmutableModel):
    kind: Literal["host"] = "host"
    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")


ResolvedEnvironmentPart = Annotated[
    LiteralEnvironmentPart | HostEnvironmentPart,
    Field(discriminator="kind"),
]


class ResolvedEnvironmentBinding(ImmutableModel):
    """One admitted process environment export compiled from a named pipeline value."""

    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
    parts: tuple[ResolvedEnvironmentPart, ...] = Field(min_length=1, max_length=32)

    @field_validator("parts")
    @classmethod
    def validate_parts(
        cls, value: tuple[ResolvedEnvironmentPart, ...]
    ) -> tuple[ResolvedEnvironmentPart, ...]:
        if all(isinstance(item, LiteralEnvironmentPart) and item.value == "" for item in value):
            return (LiteralEnvironmentPart(value=""),)
        return value
