from __future__ import annotations

from typing import Any

from pydantic import Field

from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.vocabulary import CommandSafety, UvRecipeId


class UvRecipeSpec(ImmutableModel):
    capability_id: str
    recipe: UvRecipeId
    label: str
    description: str
    safety: CommandSafety
    parallel_safe: bool = True
    requires_uv_project: bool = True
    produces_security_findings: bool = False
    produces_sbom: bool = False


class SecurityFinding(ImmutableModel):
    advisory_id: str
    package: str | None = None
    version: str | None = None
    aliases: tuple[str, ...] = Field(default_factory=tuple)
    fixed_versions: tuple[str, ...] = Field(default_factory=tuple)
    summary: str | None = None


class UvRecipeResult(ImmutableModel):
    recipe: UvRecipeId
    safety: CommandSafety
    argv: tuple[str, ...]
    exit_code: int
    output: str = ""
    findings: tuple[SecurityFinding, ...] = Field(default_factory=tuple)
    structured_output: dict[str, Any] | list[Any] | None = None
    sbom_component_count: int | None = None
