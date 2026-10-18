"""UX-C read-only Plan drafts through the ONE canonical compiler and source graph.

This module does not run Plans, save Plans, reread caller input files or admit work.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import yaml

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.fleet_plan_composition import coerce_input
from oh_goon.core.models.fleet_plan_composition import PlanInputValue
from oh_goon.core.template_identity import template_classification, template_reference_allowed
from oh_goon.integrations.fleet_plan_compilation import (
    parse_fleet_yaml_document,
    parse_plan_template,
)
from oh_goon.runtime.goon_templates import GoonTemplateStore
from oh_goon.runtime.plan_templates import PlanTemplateStore
from oh_goon.services.fleet_plan_input_inspection import inspect_projected_inputs
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
from oh_goon.services.template_plan_draft_comparison import compare_resolved_drafts


@dataclass(frozen=True, slots=True)
class TemplatePlanDraft:
    """Proposed YAML and safe diagnostics, never authorized Run/source state."""

    plan_yaml: str
    values_yaml: str
    report: dict[str, Any]


class _FrozenGoonSources:
    """One selected source snapshot per imported package in this proposal."""

    def __init__(self, store: GoonTemplateStore) -> None:
        self._store = store
        self._snapshots: dict[str, Any] = {}

    def keys(self) -> tuple[str, ...]:
        return self._store.keys()

    def source(self, key: str) -> Any:
        if key not in self._snapshots:
            self._snapshots[key] = self._store.source(key)
        return self._snapshots[key]


def _object(value: object, label: str) -> dict[str, Any]:
    if type(value) is not dict or not all(type(k) is str for k in value):
        raise FleetPlanInvalid(f"UX-C {label} requires a mapping with string keys")
    return value


def _edit(items: list[object], action: Mapping[str, Any]) -> None:
    op = action.get("op")
    if op in ("add", "replace"):
        if set(action) != {"op", "at", "import"}:
            raise FleetPlanInvalid(f"UX-C {op} requires exactly op, at, import")
        at, item = action["at"], action["import"]
        limit = len(items) if op == "add" else len(items) - 1
        if type(at) is not int or not 0 <= at <= limit:
            raise FleetPlanInvalid("UX-C import position is invalid")
        if type(item) not in (str, dict):
            raise FleetPlanInvalid("UX-C import must be a literal key or mapping")
        if isinstance(item, dict) and (
            set(item) - {"import", "as", "with", "pass", "failure_impact"}
        ):
            raise FleetPlanInvalid("UX-C unknown import or executable field")
        if op == "add":
            items.insert(at, item)
        else:
            items[at] = item
    elif op == "remove":
        if (
            set(action) != {"op", "at"}
            or type(action["at"]) is not int
            or not 0 <= action["at"] < len(items)
        ):
            raise FleetPlanInvalid("UX-C invalid remove operation")
        items.pop(action["at"])
    elif op == "move":
        if set(action) != {"op", "from", "to"}:
            raise FleetPlanInvalid("UX-C move requires op, from, to")
        src, dst = action["from"], action["to"]
        if not all(type(i) is int and 0 <= i < len(items) for i in (src, dst)):
            raise FleetPlanInvalid("UX-C invalid move positions")
        items.insert(dst, items.pop(src))
    else:
        raise FleetPlanInvalid("UX-C supports only add, replace, remove, move")


def _values_yaml(values: Mapping[str, object], inspections: tuple) -> str:
    definitions = {i.definition.name: i.definition for i in inspections}
    if len(values) > 64 or set(values) - set(definitions):
        raise FleetPlanInvalid("UX-C unknown caller value or more than 64 caller names")
    typed: dict[str, PlanInputValue] = {}
    for name, value in values.items():
        if type(value) not in (str, bool, int, type(None)):
            raise FleetPlanInvalid(f"UX-C caller value {name!r} must be scalar")
        if isinstance(value, str) and "${{" in value:
            raise FleetPlanInvalid("UX-C caller values cannot contain expressions")
        typed[name] = coerce_input(definitions[name], value, context="UX-C draft")
    result = yaml.safe_dump({"inputs": typed}, allow_unicode=True, sort_keys=False)
    if len(result.encode("utf-8")) > 65536:
        raise FleetPlanInvalid("UX-C values draft exceeds caller-file limit")
    return result


def propose_plan(
    *,
    key: str,
    expected_sha256: str,
    edits: Sequence[Mapping[str, Any]] = (),
    values: Mapping[str, object] | None = None,
    projected_inputs: Sequence[str] | None = None,
    plans: PlanTemplateStore | None = None,
    goons: GoonTemplateStore | None = None,
) -> TemplatePlanDraft:
    """Edit a frozen shipped Plan *in memory*; validate with canonical Fleet owners."""
    plan_store = plans or PlanTemplateStore()
    goon_store = goons or GoonTemplateStore()
    source = plan_store.source(key)
    if type(expected_sha256) is not str or source.sha256 != expected_sha256:
        raise FleetPlanInvalid("UX-C stale Plan source; refresh original revision")
    if not isinstance(edits, (list, tuple)) or len(edits) > 64:
        raise FleetPlanInvalid("UX-C edits must be at most 64 operations")
    original = parse_fleet_yaml_document(source.content, source=str(source.path)).payload
    items = original.get("goons")
    if not isinstance(items, list):
        raise FleetPlanInvalid("UX-C original Plan has no ordered Goon list")
    proposal = dict(original)
    proposed_items = list(items)
    for action in edits:
        _edit(proposed_items, _object(action, "edit"))
    if not proposed_items:
        raise FleetPlanInvalid("UX-C Plan must retain a nonempty ordered Goon list")
    proposal["goons"] = proposed_items
    if projected_inputs is not None:
        if type(projected_inputs) not in (list, tuple) or any(
            type(x) is not str for x in projected_inputs
        ):
            raise FleetPlanInvalid("UX-C projected inputs must be a list of names")
        if projected_inputs:
            proposal["inputs"] = list(projected_inputs)
        else:
            proposal.pop("inputs", None)
    plan_yaml = yaml.safe_dump(proposal, allow_unicode=True, sort_keys=False)
    compiled = parse_plan_template(
        plan_yaml, source="UX-C draft", expected_name=source.path.parent.name
    )
    owner_class = template_classification(key, kind="Plan")
    for step in compiled.steps:
        child_class = template_classification(step.goon, kind="Goon")
        if not template_reference_allowed(owner_class, child_class):
            raise FleetPlanInvalid(
                f"UX-C {owner_class.value} Plan cannot import {child_class.value} Goon {step.goon!r}"
            )
    # One original source-graph builder, no new import/resolution semantics.
    resolver = FleetPlanResolver(goon_templates=_FrozenGoonSources(goon_store))
    graph = resolver.analyze_source(source.path, compiled_overrides={source.path: compiled})
    original_graph = resolver.analyze_source(
        source.path, compiled_overrides={source.path: source.compiled}
    )
    inspected = inspect_projected_inputs(graph)
    selected = _object(values if values is not None else {}, "values")
    caller_yaml = _values_yaml(selected, inspected)
    definitions = {i.definition.name: i.definition for i in inspected}
    missing = sorted(n for n, d in definitions.items() if d.required and n not in selected)
    original_inspected = inspect_projected_inputs(original_graph)
    original_defs = {i.definition.name: i.definition for i in original_inspected}
    original_missing = any(d.required and name not in selected for name, d in original_defs.items())
    # Canonical source graph + resolver do all semantic work. Comparison only
    # projects safe step identities/types and hashed capability contracts.
    resolved_comparison = compare_resolved_drafts(
        original=original_graph,
        proposed=graph,
        resolver=resolver,
        values={
            n: coerce_input(d, selected[n], context="UX-C comparison")
            for n, d in definitions.items()
            if n in selected
        },
        original_names=set(original_defs),
        proposed_names=set(definitions),
        original_missing=original_missing,
        proposed_missing=bool(missing),
    )
    old = [(s.step_id, s.goon) for s in source.compiled.steps]
    new = [(s.step_id, s.goon) for s in compiled.steps]
    imports = sorted({(s.goon, graph.goon(source.path, s).source_sha256) for s in compiled.steps})
    change_classes: list[str] = []
    if hashlib.sha256(plan_yaml.encode()).hexdigest() != source.sha256:
        change_classes.append("source_changed")
    if old != new:
        change_classes.append("ordering_changed")
    if tuple(source.compiled.projected_inputs) != tuple(compiled.projected_inputs):
        change_classes.append("recipient_changed")
    if selected:
        change_classes.append("values_selected")
    if resolved_comparison["status"] == "UNRESOLVED_REQUIRED_INPUTS":
        change_classes.append("unresolved/blocked")
    if resolved_comparison["capabilities_changed"]:
        change_classes.append("capabilities_changed")
    if not change_classes:
        change_classes.append("presentation_only")
    report = {
        "schema": "oh-goon.template-plan-draft/v1",
        "status": "DRAFT_ONLY_NOT_ADMITTED",
        "plan": key,
        "original_root_sha256": source.sha256,
        "proposed_root_sha256": hashlib.sha256(plan_yaml.encode()).hexdigest(),
        "closure_digest": None,
        "imported_sources": [{"key": k, "sha256": digest} for k, digest in imports],
        "before": [{"id": i, "goon": g} for i, g in old],
        "after": [{"id": i, "goon": g} for i, g in new],
        "ordering_changed": old != new,
        "change_classes": change_classes,
        "resolved_comparison": resolved_comparison,
        "projected_inputs_changed": tuple(source.compiled.projected_inputs)
        != tuple(compiled.projected_inputs),
        "recipients": [
            {
                "name": i.definition.name,
                "type": i.definition.input_type.value,
                "required": i.definition.required,
                "owners": [list(p) for p in i.owner_paths],
            }
            for i in inspected
        ],
        "missing_required_inputs": missing,
        "selected_value_names": sorted(selected),
        "preview_ready": not missing,
        "execution_permission": "NONE",
        "save_permission": "NONE",
        "authority": "original compiler + source graph; diagnostic projection only",
    }
    return TemplatePlanDraft(plan_yaml, caller_yaml, report)
