from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import yaml

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.fleet_plan import CommandPlanStep, GitPlanStep
from oh_goon.integrations.fleet_plan_compilation import parse_goon_template
from oh_goon.integrations.fleet_plan_source_snapshot import read_fleet_plan_source


@pytest.mark.parametrize("field", ["name", "description"])
def test_goon_rejects_source_metadata_even_when_it_matches_the_package(field: str) -> None:
    payload = {"version": 1, field: "package-name", "steps": [{"run": "echo ok"}]}
    with pytest.raises(FleetPlanInvalid, match=f"unsupported fields: {field}"):
        parse_goon_template(yaml.safe_dump(payload), expected_name="package-name")


def test_derived_metadata_keeps_exact_source_byte_identity(tmp_path: Path) -> None:
    path = tmp_path / "goon.yml"
    content = b"steps:\n- run: echo ok\n"
    path.write_bytes(content)
    snapshot = read_fleet_plan_source(
        path, kind="goon", source="untrusted-readable-label", expected_name="package-name"
    )
    assert snapshot.compiled.name == "package-name"
    assert snapshot.compiled.description == ""
    assert snapshot.content.encode("utf-8") == content
    assert snapshot.sha256 == hashlib.sha256(content).hexdigest()


@pytest.mark.parametrize(
    "value,kind,required",
    [
        (True, "boolean", False),
        (False, "boolean", False),
        (2, "integer", False),
        (0, "integer", False),
        ("2", "string", False),
        (None, "string", True),
    ],
)
def test_scalar_input_defaults_keep_native_types(value: object, kind: str, required: bool) -> None:
    compiled = parse_goon_template(
        yaml.safe_dump({"inputs": {"value": value}, "steps": [{"run": "echo ok"}]})
    )
    definition = compiled.inputs[0]
    assert definition.input_type.value == kind
    assert definition.default == value
    assert type(definition.default) is type(value)
    assert definition.required is required


@pytest.mark.parametrize("value", [2.5, ["value"]])
def test_unsupported_input_defaults_are_rejected_without_conversion(value: object) -> None:
    with pytest.raises(
        FleetPlanInvalid, match="input definitions must be mappings, scalar defaults, or null"
    ):
        parse_goon_template(
            yaml.safe_dump({"inputs": {"value": value}, "steps": [{"run": "echo ok"}]})
        )


@pytest.mark.parametrize(
    "definition",
    [
        {"name": "renamed", "default": "value"},
        {"input_type": "integer", "default": 2},
        {"type": "integer", "minimum": 1, "default": 2},
        {"type": "integer", "maximum": 3, "default": 2},
        {"input_type": "string", "type": "integer", "default": 2},
        {"type": "integer", "minimum": 100, "min": 1, "default": 2},
        {"type": "integer", "maximum": 200, "max": 3, "default": 2},
    ],
)
def test_input_contract_rejects_internal_fields_before_they_can_override_authoring(
    definition: dict[str, object],
) -> None:
    payload = {"version": 1, "inputs": {"value": definition}, "steps": [{"run": "echo ok"}]}
    with pytest.raises(FleetPlanInvalid, match="input definitions contain internal fields"):
        parse_goon_template(yaml.safe_dump(payload))


@pytest.mark.parametrize(
    "field,value",
    [
        ("env", [{"name": "TOKEN", "source": {"kind": "host"}}]),
        ("outputs", [{"name": "PUBLIC", "source": "${{ steps.producer.outputs.VALUE }}"}]),
    ],
)
def test_goon_named_declarations_use_one_mapping_form(field: str, value: object) -> None:
    payload = {"version": 1, field: value, "steps": [{"run": "echo ok"}]}
    with pytest.raises(FleetPlanInvalid, match=f"{field} must be a mapping"):
        parse_goon_template(yaml.safe_dump(payload))


@pytest.mark.parametrize(
    "outputs,message",
    [
        ([{"name": "VALUE", "path": "value.txt"}], "outputs must be a mapping"),
        ({"VALUE": {"name": "RENAMED", "path": "value.txt"}}, "remove inner name"),
    ],
)
def test_step_output_name_has_one_authoring_owner(outputs: object, message: str) -> None:
    payload = {"version": 1, "steps": [{"run": "echo ok", "outputs": outputs}]}
    with pytest.raises(FleetPlanInvalid, match=message):
        parse_goon_template(yaml.safe_dump(payload))


@pytest.mark.parametrize("top_level_uv", [False, True])
def test_step_uv_cannot_bypass_or_override_the_atomic_environment(top_level_uv: bool) -> None:
    payload: dict[str, object] = {
        "version": 1,
        "steps": [{"run": "uv run pytest", "uv": {"project": "other", "group": "production"}}],
    }
    if top_level_uv:
        payload["uv"] = {"group": "dev"}
    with pytest.raises(FleetPlanInvalid, match="must select uv at the Atomic Goon root"):
        parse_goon_template(yaml.safe_dump(payload))


@pytest.mark.parametrize("authored_from", [False, True])
def test_workspace_rejects_compiled_source_instead_of_selecting_between_forms(
    authored_from: bool,
) -> None:
    workspace: dict[str, object] = {"source": {"kind": "repository"}}
    if authored_from:
        workspace["from"] = "repository"
    payload = {"version": 1, "steps": [{"run": "echo ok", "workspace": workspace}]}
    with pytest.raises(FleetPlanInvalid, match="source is an internal compiled field"):
        parse_goon_template(yaml.safe_dump(payload))


@pytest.mark.parametrize("flat_args", [False, True])
def test_git_rejects_compiled_action_instead_of_silently_overwriting_it(flat_args: bool) -> None:
    step: dict[str, object] = {
        "uses": "git",
        "timeout_seconds": 30,
        "action": {"operation": "native", "args": ["push"]},
    }
    if flat_args:
        step["args"] = ["status", "--short"]
    with pytest.raises(FleetPlanInvalid, match="action is an internal compiled field"):
        parse_goon_template(yaml.safe_dump({"version": 1, "steps": [step]}))


def test_canonical_named_contract_and_uv_dataflow_still_lower_to_execution_models() -> None:
    compiled = parse_goon_template(
        """version: 1
inputs:
  count: {type: integer, min: 1, max: 3, default: 2, description: Number of checks}
env:
  TOKEN: {from: host}
outputs:
  RESULT: ${{ steps.producer.outputs.VALUE }}
uv: {project: backend, group: dev}
steps:
- id: producer
  run: uv run prepare
  outputs: {VALUE: value.txt}
  workspace: {from: repository, publish: true}
- run: uv run consume ${{ inputs.count }}
  imports: {env: [TOKEN]}
  workspace: {from: step, step: producer}
"""
    )
    definition = compiled.inputs[0]
    assert (
        definition.name,
        definition.input_type.value,
        definition.minimum,
        definition.maximum,
    ) == ("count", "integer", 1, 3)
    assert compiled.env[0].source.kind == "host"
    assert compiled.outputs[0].name == "RESULT"
    producer, consumer = compiled.steps
    assert isinstance(producer, CommandPlanStep) and isinstance(consumer, CommandPlanStep)
    assert producer.uv is not None and producer.uv.project == "backend"
    assert producer.argv == (
        "uv",
        "run",
        "--frozen",
        "--no-default-groups",
        "--project",
        "backend",
        "--group",
        "dev",
        "prepare",
    )
    assert producer.workspace.publish is True
    assert consumer.workspace.source.kind == "step"
    assert consumer.workspace.source.step_id == "producer"


def test_native_git_and_explicit_security_cache_facts_remain_canonical_capabilities() -> None:
    compiled = parse_goon_template(
        """version: 1
steps:
- uses: git
  args: [status, --short]
  timeout_seconds: 30
  workspace: {from: repository}
  cache: {source: {kind: empty}}
  capabilities: {egress: {mode: none}}
"""
    )
    step = compiled.steps[0]
    assert isinstance(step, GitPlanStep)
    assert step.action.operation.value == "native"
    assert step.action.args == ("status", "--short")
    assert step.capabilities.egress.mode == "none"
    assert step.cache.source.kind == "empty"
