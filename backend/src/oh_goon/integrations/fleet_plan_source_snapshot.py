from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Literal

from yaml.nodes import MappingNode, ScalarNode, SequenceNode

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.fleet_plan import CompiledExecutionTemplate
from oh_goon.core.template_identity import catalog_template_name
from oh_goon.integrations.fleet_plan_compilation import (
    compile_goon_document,
    compile_plan_document,
    parse_fleet_yaml_document,
)


@dataclass(frozen=True, slots=True)
class FleetPlanStepPosition:
    """One YAML-authored step position from the exact parsed source snapshot."""

    line: int
    column: int


@dataclass(frozen=True, slots=True)
class FleetPlanSourceSnapshot:
    """One exact filesystem byte snapshot and the compiled execution contract from those bytes."""

    path: Path
    content: str
    sha256: str
    compiled: CompiledExecutionTemplate
    step_positions: Mapping[str, FleetPlanStepPosition]
    value_positions: Mapping[str, FleetPlanStepPosition] = field(default_factory=dict)


def read_fleet_plan_source(
    path: Path,
    *,
    kind: Literal["goon", "plan"],
    source: str | None = None,
    expected_name: str | None = None,
) -> FleetPlanSourceSnapshot:
    """Read once, then decode, hash and parse that same immutable byte snapshot."""
    try:
        raw = path.read_bytes()
        content = raw.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise FleetPlanInvalid(f"Cannot read Fleet YAML source {path}: {exc}") from exc

    label = source or str(path)
    if expected_name is None:
        expected_name = catalog_template_name(path, kind=kind)
    document = parse_fleet_yaml_document(content, source=label)
    compiled = (
        compile_goon_document(document, source=label, expected_name=expected_name)
        if kind == "goon"
        else compile_plan_document(document, source=label, expected_name=expected_name)
    )
    return FleetPlanSourceSnapshot(
        path=path,
        content=content,
        sha256=hashlib.sha256(raw).hexdigest(),
        compiled=compiled,
        step_positions=MappingProxyType(_step_positions(document.root, compiled)),
        value_positions=MappingProxyType(_value_positions(document.root, compiled)),
    )


def _value_positions(
    root: MappingNode, compiled: CompiledExecutionTemplate
) -> dict[str, FleetPlanStepPosition]:
    positions: dict[str, FleetPlanStepPosition] = {}
    for namespace in ("inputs", "env"):
        node = _mapping_value(root, namespace)
        if isinstance(node, MappingNode):
            for key, definition in node.value:
                if isinstance(key, ScalarNode):
                    positions[f"{namespace}/{key.value}"] = FleetPlanStepPosition(
                        line=key.start_mark.line + 1, column=key.start_mark.column + 1
                    )
                    if namespace == "inputs":
                        default = None
                        if isinstance(definition, MappingNode):
                            default = _mapping_value(definition, "default")
                        elif (
                            isinstance(definition, ScalarNode)
                            and definition.tag != "tag:yaml.org,2002:null"
                        ):
                            default = definition
                        if default is not None:
                            positions[f"inputs/{key.value}/default"] = FleetPlanStepPosition(
                                line=default.start_mark.line + 1,
                                column=default.start_mark.column + 1,
                            )
    imports = _mapping_value(root, "goons")
    if isinstance(imports, SequenceNode):
        for node, step in zip(imports.value, compiled.steps, strict=False):
            positions[f"import/{step.step_id}"] = FleetPlanStepPosition(
                line=node.start_mark.line + 1, column=node.start_mark.column + 1
            )
            if isinstance(node, MappingNode):
                bindings = _mapping_value(node, "with")
                if isinstance(bindings, MappingNode):
                    for key, value in bindings.value:
                        if isinstance(key, ScalarNode):
                            positions[f"binding/{step.step_id}/{key.value}"] = (
                                FleetPlanStepPosition(
                                    line=value.start_mark.line + 1,
                                    column=value.start_mark.column + 1,
                                )
                            )
                forwarding = _mapping_value(node, "pass")
                if isinstance(forwarding, SequenceNode):
                    for name in forwarding.value:
                        if isinstance(name, ScalarNode):
                            positions[f"binding/{step.step_id}/{name.value}"] = (
                                FleetPlanStepPosition(
                                    line=name.start_mark.line + 1, column=name.start_mark.column + 1
                                )
                            )
    return positions


def _step_positions(
    root: MappingNode, compiled: CompiledExecutionTemplate
) -> dict[str, FleetPlanStepPosition]:
    """Derive authored/generated Step marks from the exact syntax tree and compiled order."""

    # Existing Atomic source positions are unchanged. An explicitly aliased
    # Goon occurrence has a new authored identity; record its exact YAML mark
    # without altering legacy no-alias Plan/Goon source-position projections.
    steps = _mapping_value(root, "steps")
    imports = steps is None
    if imports:
        steps = _mapping_value(root, "goons")
    if not isinstance(steps, SequenceNode):
        return {}
    positions: dict[str, FleetPlanStepPosition] = {}
    for item, compiled_step in zip(steps.value, compiled.steps, strict=False):
        if not isinstance(item, MappingNode):
            continue
        if imports and _mapping_value(item, "as") is None:
            continue
        positions[compiled_step.step_id] = FleetPlanStepPosition(
            line=item.start_mark.line + 1,
            column=item.start_mark.column + 1,
        )
    return positions


def _mapping_value(node: MappingNode, key: str):
    for key_node, value_node in node.value:
        if isinstance(key_node, ScalarNode) and key_node.value == key:
            return value_node
    return None
