from __future__ import annotations

import pytest
from pydantic import ValidationError

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.fleet_plan_composition import coerce_input
from oh_goon.core.models.fleet_plan_composition import PlanInputDefinition, PlanInputType
from oh_goon.core.models.fleet_plan_requirements import PyprojectValueRequirement


@pytest.mark.parametrize(
    ("definition", "message"),
    [
        (
            {"name": "x", "input_type": "integer", "minimum": 2, "maximum": 1},
            "minimum cannot exceed",
        ),
        ({"name": "x", "input_type": "choice"}, "require at least one choice"),
        ({"name": "x", "input_type": "choice", "choices": ("a", "a")}, "choices must be unique"),
        ({"name": "x", "input_type": "string", "choices": ("a",)}, "only valid for type=choice"),
        ({"name": "x", "input_type": "string", "minimum": 1}, "only valid for type=integer"),
        ({"name": "x", "input_type": "boolean", "non_empty": True}, "only for type=string"),
        ({"name": "x", "input_type": "integer", "pattern": "x"}, "only for type=string"),
        ({"name": "x", "input_type": "string", "pattern": "["}, "pattern is invalid"),
        (
            {"name": "x", "input_type": "string", "required": True, "default": "x"},
            "required inputs cannot also declare a default",
        ),
        ({"name": "x", "input_type": "string", "default": 1}, "default must be a string"),
        (
            {"name": "x", "input_type": "string", "non_empty": True, "default": "   "},
            "default must be non-empty",
        ),
        (
            {"name": "x", "input_type": "string", "pattern": "[a-z]+", "default": "BAD"},
            "default must match",
        ),
        ({"name": "x", "input_type": "boolean", "default": "true"}, "must be true or false"),
        ({"name": "x", "input_type": "integer", "default": "1"}, "must be an integer"),
        (
            {"name": "x", "input_type": "integer", "minimum": 2, "default": 1},
            "default must be >= 2",
        ),
        (
            {"name": "x", "input_type": "integer", "maximum": 2, "default": 3},
            "default must be <= 2",
        ),
        (
            {"name": "x", "input_type": "choice", "choices": ("a", "b"), "default": "c"},
            "default must be one of",
        ),
    ],
)
def test_input_definition_rejects_invalid_contracts(
    definition: dict[str, object], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        PlanInputDefinition.model_validate(definition)


def test_pyproject_pattern_constraints_are_string_only_and_valid_regex() -> None:
    with pytest.raises(ValidationError, match="pattern is valid only for string"):
        PyprojectValueRequirement(field="project.version", value_type="integer", pattern=r"\d+")
    with pytest.raises(ValidationError, match="pattern is invalid"):
        PyprojectValueRequirement(field="project.version", value_type="string", pattern="[")
    with pytest.raises(ValidationError, match="non_empty is valid only for string"):
        PyprojectValueRequirement(field="project.version", value_type="boolean", non_empty=True)


def test_runtime_input_coercion_fails_closed_for_each_type() -> None:
    string = PlanInputDefinition(name="tag", non_empty=True, pattern=r"[a-z]+")
    boolean = PlanInputDefinition(name="enabled", input_type="boolean")
    integer = PlanInputDefinition(name="workers", input_type="integer", minimum=1, maximum=4)
    choice = PlanInputDefinition(name="mode", input_type="choice", choices=("fast", "strict"))
    required = PlanInputDefinition(name="required", required=True)

    with pytest.raises(FleetPlanInvalid, match="is required"):
        coerce_input(required, None, context="test")
    with pytest.raises(FleetPlanInvalid, match="must be a string"):
        coerce_input(string, 1, context="test")
    with pytest.raises(FleetPlanInvalid, match="must be non-empty"):
        coerce_input(string, " ", context="test")
    with pytest.raises(FleetPlanInvalid, match="must match"):
        coerce_input(string, "BAD", context="test")
    with pytest.raises(FleetPlanInvalid, match="true or false"):
        coerce_input(boolean, "yes", context="test")
    with pytest.raises(FleetPlanInvalid, match="must be an integer"):
        coerce_input(integer, "1.5", context="test")
    with pytest.raises(FleetPlanInvalid, match="must be >= 1"):
        coerce_input(integer, 0, context="test")
    with pytest.raises(FleetPlanInvalid, match="must be <= 4"):
        coerce_input(integer, 5, context="test")
    with pytest.raises(FleetPlanInvalid, match="must be one of"):
        coerce_input(choice, "unknown", context="test")

    assert coerce_input(boolean, "TRUE", context="test") is True
    assert coerce_input(integer, "+3", context="test") == 3
    assert coerce_input(choice, "strict", context="test") == "strict"


def test_optional_null_input_remains_compile_time_null() -> None:
    optional = PlanInputDefinition(name="optional")
    assert coerce_input(optional, None, context="test") is None


@pytest.mark.parametrize(
    ("input_type", "value"),
    [
        ("hostname", "registry.example.com"),
        ("repository_path", "services/api"),
        ("repository_path", "."),
        ("git_revision", "abcdef1"),
        ("digest", "sha256:" + "a" * 64),
        ("semver", "1.2.3"),
        ("semver", "1.2.3-rc.1+build.7"),
        ("duration", "30s"),
        ("kubernetes_name", "api-server"),
        ("kubernetes_deployment_name", "api-server"),
        ("kubernetes_deployment_name", "api.service"),
        ("oci_repository", "team/app"),
        (
            "oci_reference",
            "registry.example.com/team/app@sha256:" + "b" * 64,
        ),
        ("oci_reference", "${{ steps.build.outputs.image }}"),
        ("workspace_reference", "workspace-1"),
    ],
)
def test_domain_input_types_accept_canonical_values(input_type: str, value: str) -> None:
    definition = PlanInputDefinition(name="x", input_type=input_type, default=value)
    assert definition.default == value


@pytest.mark.parametrize(
    ("input_type", "value"),
    [
        ("hostname", "Bad Host"),
        ("repository_path", "../escape"),
        ("repository_path", "services//api"),
        ("git_revision", "not-a-revision"),
        ("digest", "sha256:short"),
        ("semver", "01.2.3"),
        ("duration", "0s"),
        ("kubernetes_name", "Api-Server"),
        ("kubernetes_deployment_name", "Api-Server"),
        ("kubernetes_deployment_name", "app..api"),
        ("kubernetes_deployment_name", "a" * 64 + ".api"),
        ("kubernetes_deployment_name", "a." * 127 + "a"),
        ("oci_repository", "Team/App"),
        ("oci_reference", "registry.example.com/team/app:latest"),
        ("workspace_reference", "-workspace"),
    ],
)
def test_domain_input_types_reject_invalid_values(input_type: str, value: str) -> None:
    with pytest.raises(ValueError, match="invalid .* syntax"):
        PlanInputDefinition(name="x", input_type=input_type, default=value)


def test_domain_input_regressions_cover_every_string_shaped_domain_type() -> None:
    primitive_types = {
        PlanInputType.STRING,
        PlanInputType.BOOLEAN,
        PlanInputType.INTEGER,
        PlanInputType.CHOICE,
    }
    assert {kind.value for kind in PlanInputType if kind not in primitive_types} == {
        "hostname",
        "repository_path",
        "git_revision",
        "digest",
        "semver",
        "duration",
        "kubernetes_name",
        "kubernetes_deployment_name",
        "oci_repository",
        "oci_reference",
        "workspace_reference",
    }


def test_pattern_remains_string_only_escape_hatch() -> None:
    with pytest.raises(ValueError, match="advanced escape hatch"):
        PlanInputDefinition(name="x", input_type="git_revision", pattern=".*")
