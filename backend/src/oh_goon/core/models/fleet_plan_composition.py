from __future__ import annotations

import re
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.models.fleet_plan_kubernetes import (
    _validate_dns_label,
    _validate_dns_subdomain,
)

PlanInputValue = str | bool | int | None


class PlanInputType(StrEnum):
    STRING = "string"
    BOOLEAN = "boolean"
    INTEGER = "integer"
    CHOICE = "choice"
    HOSTNAME = "hostname"
    REPOSITORY_PATH = "repository_path"
    GIT_REVISION = "git_revision"
    DIGEST = "digest"
    SEMVER = "semver"
    DURATION = "duration"
    KUBERNETES_NAME = "kubernetes_name"
    KUBERNETES_DEPLOYMENT_NAME = "kubernetes_deployment_name"
    OCI_REPOSITORY = "oci_repository"
    OCI_REFERENCE = "oci_reference"
    WORKSPACE_REFERENCE = "workspace_reference"
    REPOSITORY_REFERENCE = "repository_reference"
    SKILL_REFERENCE = "skill_reference"
    ACCEPTANCE_PROFILE = "acceptance_profile"


class PlanInputDefinition(ImmutableModel):
    """One typed compile-time Plan/Goon input.

    Inputs are authoring-time values only and are fully bound before a
    ResolvedFleetPlan is admitted. Authoring derives ``required`` from the
    absence of both ``default`` and ``optional: true``.
    """

    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_-]{0,63}$")
    input_type: PlanInputType = PlanInputType.STRING
    default: PlanInputValue = None
    required: bool = False
    description: str = Field(default="", max_length=300)
    choices: tuple[str, ...] = Field(default_factory=tuple, max_length=32)
    minimum: int | None = None
    maximum: int | None = None
    non_empty: bool = False
    pattern: str | None = Field(default=None, min_length=1, max_length=240)
    starts_with: str | None = Field(default=None, min_length=1, max_length=240)

    @model_validator(mode="after")
    def validate_contract(self) -> PlanInputDefinition:
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("Fleet integer input minimum cannot exceed maximum")

        if self.input_type is PlanInputType.CHOICE:
            if not self.choices:
                raise ValueError("Fleet choice inputs require at least one choice")
            if len(self.choices) != len(set(self.choices)):
                raise ValueError("Fleet choice input choices must be unique")
        elif self.choices:
            raise ValueError("Fleet input choices are only valid for type=choice")

        if self.input_type is not PlanInputType.INTEGER and (
            self.minimum is not None or self.maximum is not None
        ):
            raise ValueError("Fleet input min/max constraints are only valid for type=integer")
        string_types = {
            PlanInputType.STRING,
            PlanInputType.HOSTNAME,
            PlanInputType.REPOSITORY_PATH,
            PlanInputType.GIT_REVISION,
            PlanInputType.DIGEST,
            PlanInputType.SEMVER,
            PlanInputType.DURATION,
            PlanInputType.KUBERNETES_NAME,
            PlanInputType.KUBERNETES_DEPLOYMENT_NAME,
            PlanInputType.OCI_REPOSITORY,
            PlanInputType.OCI_REFERENCE,
            PlanInputType.WORKSPACE_REFERENCE,
            PlanInputType.REPOSITORY_REFERENCE,
            PlanInputType.SKILL_REFERENCE,
            PlanInputType.ACCEPTANCE_PROFILE,
        }
        if self.non_empty and self.input_type not in string_types:
            raise ValueError(
                "Fleet input non_empty is valid only for type=string or string-shaped domain inputs"
            )
        if self.pattern is not None:
            if self.input_type is not PlanInputType.STRING:
                raise ValueError(
                    "Fleet input pattern is an advanced escape hatch valid only for type=string"
                )
            try:
                re.compile(self.pattern)
            except re.error as exc:
                raise ValueError(f"Fleet input pattern is invalid: {exc}") from exc
        if self.starts_with is not None and self.input_type is not PlanInputType.STRING:
            raise ValueError("Fleet input starts_with is valid only for type=string")

        if self.required and self.default is not None:
            raise ValueError("Fleet required inputs cannot also declare a default")
        if self.default is not None:
            _validate_definition_value(self, self.default, label="default")
        return self


class EnvInputSource(ImmutableModel):
    kind: Literal["env"] = "env"
    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")


class YamlInputSource(ImmutableModel):
    kind: Literal["yaml"] = "yaml"
    path: str = Field(min_length=1, max_length=240)
    key: str = Field(
        pattern=r"^[A-Za-z_][A-Za-z0-9_-]*(?:\.[A-Za-z_][A-Za-z0-9_-]*)*$",
        max_length=240,
    )


class TomlInputSource(ImmutableModel):
    kind: Literal["toml"] = "toml"
    path: str = Field(min_length=1, max_length=240)
    key: str = Field(
        pattern=r"^[A-Za-z_][A-Za-z0-9_-]*(?:\.[A-Za-z_][A-Za-z0-9_-]*)*$",
        max_length=240,
    )


class TextInputSource(ImmutableModel):
    kind: Literal["text"] = "text"
    path: str = Field(min_length=1, max_length=240)


class GitInputSource(ImmutableModel):
    kind: Literal["git"] = "git"
    field: Literal["commit_sha", "commit_short_sha", "branch"]


PlanInputSource = Annotated[
    EnvInputSource | YamlInputSource | TomlInputSource | TextInputSource | GitInputSource,
    Field(discriminator="kind"),
]


class PlanInputBinding(ImmutableModel):
    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_-]{0,63}$")
    value: PlanInputValue = None
    sources: tuple[PlanInputSource, ...] = Field(default_factory=tuple, max_length=8)

    @model_validator(mode="after")
    def validate_binding_mode(self) -> PlanInputBinding:
        if self.sources and self.value is not None:
            raise ValueError("Fleet input binding cannot combine literal value and from sources")
        return self

    @field_validator("value")
    @classmethod
    def validate_value(cls, value: PlanInputValue) -> PlanInputValue:
        if isinstance(value, str) and len(value) > 1000:
            raise ValueError("Fleet template input binding cannot exceed 1000 characters")
        return value


class PlanStepInclusion(ImmutableModel):
    """Compile-time inclusion rule lifted from one YAML step's include_when block."""

    step_id: str = Field(min_length=1, max_length=80)
    input: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_-]{0,63}$")
    equals: PlanInputValue | None = None
    not_equals: PlanInputValue | None = None

    @model_validator(mode="after")
    def validate_operator(self) -> PlanStepInclusion:
        configured = int(self.equals is not None) + int(self.not_equals is not None)
        if configured != 1:
            raise ValueError("Fleet include_when must declare exactly one of equals or not_equals")
        return self


_GOON_OUTPUT_REFERENCE = re.compile(
    r"^\$\{\{\s*(?:steps|goons)\.([A-Za-z0-9_.\-\[\]=]+)\.outputs\."
    r"([A-Za-z_][A-Za-z0-9_-]{0,63})\s*\}\}$"
)


class GoonOutputDefinition(ImmutableModel):
    """Public Goon output mapped to one internal step output reference."""

    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_-]{0,63}$")
    source: str = Field(min_length=1, max_length=240)

    @field_validator("source")
    @classmethod
    def validate_source_shape(cls, value: str) -> str:
        if _GOON_OUTPUT_REFERENCE.fullmatch(value) is None:
            raise ValueError(
                "Goon outputs must reference one complete Step output: "
                "${{ steps.<step>.outputs.<name> }} or ${{ goons.<goon>.outputs.<name> }}"
            )
        return value


def validate_template_contract(
    inputs: tuple[PlanInputDefinition, ...],
    outputs: tuple[GoonOutputDefinition, ...],
    inclusions: tuple[PlanStepInclusion, ...],
    step_ids: tuple[str, ...],
) -> None:
    """Validate authoring-only Plan/Goon names and local references."""
    _require_unique(step_ids, "Fleet plan step_id values must be unique")
    input_names = tuple(item.name for item in inputs)
    _require_unique(input_names, "Fleet plan input names must be unique")
    _require_unique(
        tuple(item.name for item in outputs),
        "Goon output names must be unique",
    )
    inclusion_steps = tuple(item.step_id for item in inclusions)
    _require_unique(
        inclusion_steps,
        "Fleet plan steps can declare only one include_when rule",
    )
    _require_declared(inclusion_steps, step_ids, "Fleet include_when references unknown steps")
    _require_declared(
        tuple(item.input for item in inclusions),
        input_names,
        "Fleet include_when references undeclared inputs",
    )
    conditional_steps = set(inclusion_steps)
    local_steps = set(step_ids)
    for output in outputs:
        match = _GOON_OUTPUT_REFERENCE.fullmatch(output.source)
        assert match is not None
        source_step = match.group(1)
        if source_step not in local_steps:
            raise ValueError(
                f"Goon output {output.name!r} references unknown local step {source_step!r}"
            )
        if source_step in conditional_steps:
            raise ValueError(
                f"Goon output {output.name!r} cannot depend on conditionally included "
                f"step {source_step!r}"
            )


def _require_unique(values: tuple[str, ...], message: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(message)


def _require_declared(values: tuple[str, ...], declared: tuple[str, ...], message: str) -> None:
    unknown = sorted(set(values) - set(declared))
    if unknown:
        raise ValueError(f"{message}: {', '.join(unknown)}")


def _validate_definition_value(
    definition: PlanInputDefinition,
    value: PlanInputValue,
    *,
    label: str,
) -> None:
    if definition.input_type in {
        PlanInputType.STRING,
        PlanInputType.HOSTNAME,
        PlanInputType.REPOSITORY_PATH,
        PlanInputType.GIT_REVISION,
        PlanInputType.DIGEST,
        PlanInputType.SEMVER,
        PlanInputType.DURATION,
        PlanInputType.KUBERNETES_NAME,
        PlanInputType.KUBERNETES_DEPLOYMENT_NAME,
        PlanInputType.OCI_REPOSITORY,
        PlanInputType.OCI_REFERENCE,
        PlanInputType.WORKSPACE_REFERENCE,
        PlanInputType.REPOSITORY_REFERENCE,
        PlanInputType.SKILL_REFERENCE,
        PlanInputType.ACCEPTANCE_PROFILE,
    }:
        if not isinstance(value, str):
            raise ValueError(
                f"Fleet {definition.input_type.value} input {definition.name} {label} "
                "must be a string"
            )
        if definition.non_empty and not value.strip():
            raise ValueError(
                f"Fleet {definition.input_type.value} input {definition.name} {label} "
                "must be non-empty"
            )
        if definition.input_type is PlanInputType.STRING:
            if definition.pattern is not None and re.fullmatch(definition.pattern, value) is None:
                raise ValueError(
                    f"Fleet string input {definition.name} {label} must match "
                    f"{definition.pattern!r}"
                )
            if definition.starts_with is not None and not value.startswith(definition.starts_with):
                raise ValueError(
                    f"Fleet string input {definition.name} {label} must start with "
                    f"{definition.starts_with!r}"
                )
            return
        validate_domain_input_value(definition, value, label=label)
        return
    if definition.input_type is PlanInputType.BOOLEAN:
        if type(value) is not bool:
            raise ValueError(f"Fleet boolean input {definition.name} {label} must be true or false")
        return
    if definition.input_type is PlanInputType.INTEGER:
        if type(value) is not int:
            raise ValueError(f"Fleet integer input {definition.name} {label} must be an integer")
        if definition.minimum is not None and value < definition.minimum:
            raise ValueError(
                f"Fleet integer input {definition.name} {label} must be >= {definition.minimum}"
            )
        if definition.maximum is not None and value > definition.maximum:
            raise ValueError(
                f"Fleet integer input {definition.name} {label} must be <= {definition.maximum}"
            )
        return
    if definition.input_type is PlanInputType.CHOICE:
        if not isinstance(value, str) or value not in definition.choices:
            choices = ", ".join(definition.choices)
            raise ValueError(
                f"Fleet choice input {definition.name} {label} must be one of: {choices}"
            )
        return
    raise AssertionError(f"Unhandled Fleet input type: {definition.input_type}")


_HOSTNAME = re.compile(
    r"^(?=.{1,253}$)"
    r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)"
    r"(?:\.(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?))*$"
)
_REPOSITORY_PATH_SEGMENT = re.compile(r"^[A-Za-z0-9._-]+$")
_GIT_REVISION = re.compile(r"^[0-9a-f]{7,64}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_SEMVER = re.compile(
    r"^(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"
    r"(?:-(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*))*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
_DURATION = re.compile(r"^[1-9]\d*(?:ms|s|m|h|d)$")
_OCI_REPOSITORY = re.compile(
    r"^[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*"
    r"(?:/[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*)*$"
)
_OCI_REFERENCE = re.compile(
    r"^(?=.{1,512}$)"
    r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?::[1-9]\d{0,4})?/"
    r"[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*"
    r"(?:/[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*)*"
    r"@sha256:[0-9a-f]{64}$"
)
_STEP_OUTPUT_REFERENCE = re.compile(
    r"^\$\{\{\s*steps\.([A-Za-z0-9_.\-\[\]=]+)\.outputs\.([A-Za-z_][A-Za-z0-9_-]{0,63})\s*\}\}$"
)
_WORKSPACE_REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
_AGENT_LOCAL_PATH = r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*"
_AGENT_LOCAL_REFERENCE = re.compile(rf"^local:{_AGENT_LOCAL_PATH}$")
_AGENT_ACCEPTANCE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
# Named skills may be namespaced; acceptance profiles remain single-segment.
_AGENT_SKILL_NAME = re.compile(r"^[a-z][a-z0-9-]{0,63}(?:/[a-z][a-z0-9-]{0,63})*$")


def validate_domain_input_value(definition: PlanInputDefinition, value: str, *, label: str) -> None:
    kind = definition.input_type
    match kind:
        case PlanInputType.HOSTNAME:
            valid = _HOSTNAME.fullmatch(value) is not None
        case PlanInputType.REPOSITORY_PATH:
            if value == ".":
                valid = True
            else:
                parts = value.split("/")
                valid = bool(parts) and all(
                    part not in {"", ".", ".."} and _REPOSITORY_PATH_SEGMENT.fullmatch(part)
                    for part in parts
                )
        case PlanInputType.GIT_REVISION:
            valid = _GIT_REVISION.fullmatch(value) is not None
        case PlanInputType.DIGEST:
            valid = _DIGEST.fullmatch(value) is not None
        case PlanInputType.SEMVER:
            valid = _SEMVER.fullmatch(value) is not None
        case PlanInputType.DURATION:
            valid = _DURATION.fullmatch(value) is not None
        case PlanInputType.KUBERNETES_NAME:
            try:
                _validate_dns_label(value, label="name")
                valid = True
            except ValueError:
                valid = False
        case PlanInputType.KUBERNETES_DEPLOYMENT_NAME:
            try:
                _validate_dns_subdomain(value, label="Deployment name")
                valid = True
            except ValueError:
                valid = False
        case PlanInputType.OCI_REPOSITORY:
            valid = _OCI_REPOSITORY.fullmatch(value) is not None
        case PlanInputType.OCI_REFERENCE:
            valid = (
                _OCI_REFERENCE.fullmatch(value) is not None
                or _STEP_OUTPUT_REFERENCE.fullmatch(value) is not None
            )
        case PlanInputType.WORKSPACE_REFERENCE:
            valid = _WORKSPACE_REFERENCE.fullmatch(value) is not None
        case PlanInputType.REPOSITORY_REFERENCE:
            valid = _AGENT_LOCAL_REFERENCE.fullmatch(value) is not None
        case PlanInputType.SKILL_REFERENCE:
            valid = _AGENT_LOCAL_REFERENCE.fullmatch(value) is not None or (
                len(value) <= 128 and _AGENT_SKILL_NAME.fullmatch(value) is not None
            )
        case PlanInputType.ACCEPTANCE_PROFILE:
            valid = _AGENT_ACCEPTANCE.fullmatch(value) is not None
        case _:
            raise AssertionError(f"Unhandled Fleet domain input type: {kind}")
    if kind in {PlanInputType.REPOSITORY_REFERENCE, PlanInputType.SKILL_REFERENCE} and ":" in value:
        path = value.split(":", 1)[1] if value.startswith("local:") else value.rsplit(":", 1)[1]
        if any(part in {".", ".."} or part.startswith(".") for part in path.split("/")):
            valid = False
    if not valid:
        raise ValueError(
            f"Fleet {kind.value} input {definition.name} {label} has invalid {kind.value} syntax"
        )
