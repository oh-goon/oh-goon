from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import Field

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.models.fleet_plan_composition import PlanInputType
from oh_goon.core.path_authority import ContainedPathSpace, canonical_path

type PlanCallerOrigin = Literal["cli", "caller_file"]


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


class PlanInputSourceObservation(ImmutableModel):
    """Outcome of the original source selection; never contains a source value/path."""

    kind: Literal["env", "yaml", "toml", "text", "git"]
    state: Literal["missing", "null", "empty", "selected", "not_evaluated"]
    name: str | None = Field(default=None, pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
    git_field: Literal["commit_sha", "commit_short_sha", "branch"] | None = None


class PlanConfigurationBinding(ImmutableModel):
    kind: Literal[
        "default",
        "optional_omission",
        "caller",
        "cli",
        "caller_file",
        "literal",
        "host",
        "parent_input",
        "expression",
        "external_source",
        "producer_workspace",
        "deferred_goon_output",
        "deferred_step_output",
    ]
    parent_scope: str | None = Field(default=None, min_length=1, max_length=160)
    parent_input: str | None = Field(default=None, pattern=r"^[A-Za-z_][A-Za-z0-9_-]{0,63}$")
    producer: str | None = Field(default=None, min_length=1, max_length=240)
    output: str | None = Field(default=None, pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
    candidates: tuple[PlanInputSourceObservation, ...] = Field(default=(), max_length=8)
    source_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    line: int | None = Field(default=None, ge=1)
    column: int | None = Field(default=None, ge=1)


class PlanConfigurationEntry(ImmutableModel):
    """Safe compile-time explanation. Values, declarations and native argv are absent."""

    scope: str | None = Field(default=None, min_length=1, max_length=160)
    namespace: PlanValueNamespace
    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_-]{0,127}$")
    source_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    line: int | None = Field(default=None, ge=1)
    column: int | None = Field(default=None, ge=1)
    input_type: PlanInputType | None = None
    required: bool | None = None
    default_declared: bool | None = None
    value_state: Literal["null", "empty", "defined", "deferred"]
    binding: PlanConfigurationBinding


class PlanConfigurationEvidence(ImmutableModel):
    """Bounded explanation retained with the Plan, outside executable identity."""

    schema_name: Literal["oh-goon.plan-configuration/v1"] = "oh-goon.plan-configuration/v1"
    root_source_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    caller_file_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    entries: tuple[PlanConfigurationEntry, ...] = Field(default=(), max_length=4096)
    omitted_entries: int = Field(default=0, ge=0)

    def with_caller_origins(
        self, origins: Mapping[str, PlanCallerOrigin], *, caller_file_sha256: str | None = None
    ) -> PlanConfigurationEvidence:
        """Attach the caller collector's captured selection, never choose values again."""
        return self.model_copy(
            update={
                "caller_file_sha256": caller_file_sha256 or self.caller_file_sha256,
                "entries": tuple(
                    entry.model_copy(
                        update={"binding": PlanConfigurationBinding(kind=origins[entry.name])}
                    )
                    if entry.scope is None
                    and entry.namespace is PlanValueNamespace.INPUT
                    and entry.binding.kind == "caller"
                    and entry.name in origins
                    else entry
                    for entry in self.entries
                ),
            }
        )


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
    configuration: PlanConfigurationEvidence | None = None
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
