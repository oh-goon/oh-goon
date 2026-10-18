from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import Field

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.path_authority import ContainedPathSpace, canonical_path


class PlanResolutionDisposition(StrEnum):
    INCLUDED = "included"
    EXPANDED = "expanded"
    OMITTED = "omitted"


class PlanResolutionEntry(ImmutableModel):
    source: str = Field(min_length=1)
    step_id: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    disposition: PlanResolutionDisposition
    compiled_step_ids: tuple[str, ...] = Field(default_factory=tuple)
    reason: str = ""


class PlanValueNamespace(StrEnum):
    INPUT = "input"
    ENV = "env"


class PlanValueResolutionEntry(ImmutableModel):
    """Non-secret compile trace for one named plan value."""

    source: str = Field(min_length=1)
    scope: str = Field(min_length=1)
    namespace: PlanValueNamespace
    name: str = Field(min_length=1)
    origin: str = Field(min_length=1)
    declared: str = ""
    resolved: str = ""
    contains_host_value: bool = False


class FleetPlanResolutionReport(ImmutableModel):
    plan_id: str
    name: str
    root: str
    values: tuple[PlanValueResolutionEntry, ...] = Field(default_factory=tuple)
    entries: tuple[PlanResolutionEntry, ...] = Field(default_factory=tuple)


class PlanCheckIssue(ImmutableModel):
    path: str
    message: str


class FleetPlanCheckReport(ImmutableModel):
    root: str
    checked_files: int = Field(ge=0)
    issues: tuple[PlanCheckIssue, ...] = Field(default_factory=tuple)

    @property
    def valid(self) -> bool:
        return not self.issues


class PlanFieldChange(ImmutableModel):
    field: str
    before: object | None = None
    after: object | None = None


class PlanStepChangeKind(StrEnum):
    ADDED = "added"
    REMOVED = "removed"
    CHANGED = "changed"


class PlanStepChange(ImmutableModel):
    step_id: str
    kind: PlanStepChangeKind
    fields: tuple[PlanFieldChange, ...] = Field(default_factory=tuple)


class FleetPlanSemanticDiff(ImmutableModel):
    before_plan_id: str
    after_plan_id: str
    plan_fields: tuple[PlanFieldChange, ...] = Field(default_factory=tuple)
    steps: tuple[PlanStepChange, ...] = Field(default_factory=tuple)

    @property
    def changed(self) -> bool:
        return bool(self.plan_fields or self.steps)


def relative_diagnostic_path(path: Path, root: Path) -> str:
    candidate = canonical_path(path)
    try:
        value = ContainedPathSpace.from_root(root).relative(
            candidate, label="Fleet plan diagnostic path"
        )
    except FleetPlanInvalid:
        return str(candidate)
    return str(value) or "."
