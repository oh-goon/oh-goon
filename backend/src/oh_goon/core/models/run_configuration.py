"""One declarative invocation selection; Plan/Goon contracts still own behavior."""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import ConfigDict, Field, field_serializer, field_validator, model_validator

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.agent_repair import AgentRepairBudget
from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.models.fleet_plan_composition import PlanInputValue
from oh_goon.core.models.fleet_plan_isolation import CicdResourcePolicy
from oh_goon.core.models.git_sources import GitSourceSelection
from oh_goon.core.template_identity import canonical_template_key

_INPUT_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_-]{0,63}\Z")
_RepositorySelectionId = Annotated[str, Field(min_length=1, max_length=160, pattern=r"^\S+$")]


class _SelectionModel(ImmutableModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class CatalogPlanSelection(_SelectionModel):
    kind: Literal["catalog-plan"]
    name: str

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        try:
            return canonical_template_key(value, kind="Plan")
        except FleetPlanInvalid as exc:
            raise ValueError("catalog Plan name must use canonical category/name") from exc


class FilePlanSelection(_SelectionModel):
    kind: Literal["file-plan"]
    path: str = Field(min_length=1, max_length=4096)


class RepositoryGoonSelection(_SelectionModel):
    kind: Literal["repository-goon"]


class GitPlanSelection(GitSourceSelection):
    kind: Literal["git-plan"]
    plan: str = Field(min_length=1, max_length=500)

    @field_validator("plan")
    @classmethod
    def exact_plan(cls, value: str) -> str:
        value = GitSourceSelection.contained_path(value)
        if value == "." or value.endswith("/") or value.rsplit("/", 1)[-1] == "goon.yml":
            raise ValueError("Git Plan selection must name an exact Plan file, not a Goon")
        return value


RunSourceSelection = Annotated[
    CatalogPlanSelection | FilePlanSelection | RepositoryGoonSelection | GitPlanSelection,
    Field(discriminator="kind"),
]


class RunTargetSelection(_SelectionModel):
    repository_id: _RepositorySelectionId | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    repository_ids: tuple[_RepositorySelectionId, ...] | None = Field(
        default=None, min_length=2, exclude_if=lambda value: value is None
    )

    @field_validator("repository_ids", mode="before")
    @classmethod
    def yaml_sequence(cls, value: object) -> object:
        return tuple(value) if type(value) is list else value

    @model_validator(mode="after")
    def validate_selection(self) -> RunTargetSelection:
        if (self.repository_id is None) == (self.repository_ids is None):
            raise ValueError("target requires exactly one of repository_id or repository_ids")
        if self.repository_ids is not None and len(set(self.repository_ids)) != len(
            self.repository_ids
        ):
            raise ValueError("target.repository_ids must contain unique repository IDs")
        return self

    def selected_ids(self) -> tuple[str, ...]:
        if self.repository_id is not None:
            return (self.repository_id,)
        assert self.repository_ids is not None
        return self.repository_ids


class RunAgentSelection(_SelectionModel):
    implementation: Literal["pi", "opencode", "codex"] | None = None
    profile: str | None = Field(default=None, min_length=1, max_length=64)
    model_profile: str | None = Field(default=None, min_length=1, max_length=64)
    acceptance: str = Field(min_length=1, max_length=64)
    repository_root: str = Field(min_length=1, max_length=4096)
    skill_root: str = Field(min_length=1, max_length=4096)

    @model_validator(mode="after")
    def validate_connection_selection(self) -> RunAgentSelection:
        if (self.implementation is None) != (self.profile is None):
            raise ValueError("native agent selection requires implementation and profile together")
        if self.implementation is not None and self.model_profile is not None:
            raise ValueError("native agents own model settings; model_profile is incompatible")
        return self

    def caller_inputs(self, inputs: dict[str, PlanInputValue]) -> dict[str, PlanInputValue]:
        if "acceptance" in inputs:
            raise ValueError("select acceptance only in agent.acceptance")
        return {**inputs, "acceptance": self.acceptance}


class RunRepairSelection(_SelectionModel):
    review_passes: int = Field(ge=1, le=32)
    write_roots: tuple[str, ...] = Field(min_length=1)
    timeout_seconds: float = Field(gt=0, le=86400, allow_inf_nan=False)
    step_timeout_seconds: float = Field(gt=0, le=21600, allow_inf_nan=False)

    @field_validator("write_roots", mode="before")
    @classmethod
    def yaml_sequence(cls, value: object) -> object:
        return tuple(value) if type(value) is list else value

    def budget(self) -> AgentRepairBudget:
        return AgentRepairBudget(
            max_attempts=self.review_passes,
            timeout_seconds=self.timeout_seconds,
            step_timeout_seconds=self.step_timeout_seconds,
        )


class RunConfiguration(_SelectionModel):
    version: Literal[1]
    source: RunSourceSelection
    target: RunTargetSelection
    inputs: tuple[tuple[str, PlanInputValue], ...] = Field(default=(), max_length=64)
    agent: RunAgentSelection | None = None
    repair: RunRepairSelection | None = None
    resources: CicdResourcePolicy | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @field_validator("version", mode="before")
    @classmethod
    def version_is_integer(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("run.yml version must be integer 1")
        return value

    @field_validator("inputs", mode="before", json_schema_input_type=dict[str, PlanInputValue])
    @classmethod
    def scalar_inputs(cls, value: object) -> object:
        if type(value) is not dict:
            raise ValueError("inputs must be one scalar mapping")
        for name, item in value.items():
            if type(name) is not str or _INPUT_NAME.fullmatch(name) is None:
                raise ValueError("inputs contains an invalid caller name")
            if type(item) not in (str, bool, int, type(None)):
                raise ValueError("caller values must be strings, integers, booleans or null")
            if isinstance(item, str) and ("${" in item or "\x00" in item):
                raise ValueError("caller values cannot contain interpolation or NUL")
        return tuple(value.items())

    @field_serializer("inputs")
    def serialized_inputs(
        self, values: tuple[tuple[str, PlanInputValue], ...]
    ) -> dict[str, PlanInputValue]:
        return dict(values)

    @model_validator(mode="after")
    def validate_groups(self) -> RunConfiguration:
        self.caller_inputs()
        if self.repair is not None and self.agent is None:
            raise ValueError("repair requires an explicit agent selection")
        if self.resources is not None:
            if all(value is None for value in self.resources.model_dump().values()):
                raise ValueError("resources requires at least one numeric resource cap")
            if self.agent is not None or self.repair is not None:
                raise ValueError("whole-Run resources do not support Agent or repair execution")
        if len(self.target.selected_ids()) > 1 and (
            isinstance(self.source, RepositoryGoonSelection)
            or self.agent is not None
            or self.repair is not None
        ):
            raise ValueError("repository-goon and Agent execution require a single repository")
        return self

    def caller_inputs(self) -> dict[str, PlanInputValue]:
        values = dict(self.inputs)
        return self.agent.caller_inputs(values) if self.agent is not None else values
