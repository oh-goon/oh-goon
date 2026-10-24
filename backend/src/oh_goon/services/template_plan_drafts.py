"""Plan authoring through the canonical compiler and source graph.

Drafts stay in memory. Explicit initialization publishes a new workflow;
neither operation runs Plans, rereads caller selections or admits effects.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import yaml

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.fleet_plan_composition import coerce_input
from oh_goon.core.models.fleet_plan import CompiledGoonImportStep
from oh_goon.core.models.fleet_plan_composition import PlanInputValue
from oh_goon.core.template_identity import template_classification, template_reference_allowed
from oh_goon.integrations.fleet_plan_compilation import (
    parse_plan_template,
)
from oh_goon.runtime.goon_templates import GoonTemplateStore
from oh_goon.runtime.plan_templates import PlanTemplateStore
from oh_goon.services.fleet_plan_input_inspection import InputInspection, inspect_projected_inputs
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
from oh_goon.services.template_plan_draft_comparison import compare_resolved_drafts


def initialize_run_configuration(
    key: str,
    output: Path,
    *,
    reviewers: Sequence[tuple[str, str]] = (),
) -> Path:
    """Author one selection, optionally a normal Composite; never admit effects."""
    import tempfile

    from oh_goon.core.models.agent_providers import DEFAULT_NATIVE_AGENT_PROFILE
    from oh_goon.core.models.fleet_plan import AgentPlanStep
    from oh_goon.core.template_identity import TemplateClassification
    from oh_goon.integrations.exact_file_write import (
        prepared_text_file,
        publish_new_text_tree,
        replace_prepared_file,
    )

    selected = output.expanduser().absolute()
    if ".." in output.parts:
        raise FleetPlanInvalid("Output cannot contain parent-directory traversal")
    for component in (selected, *selected.parents):
        if component.is_symlink():
            raise FleetPlanInvalid("Output cannot contain symlink components")
    if selected.exists():
        raise FleetPlanInvalid("Output already exists; choose a fresh destination")
    if template_classification(key, kind="Plan") is TemplateClassification.INTERNAL_SELF_HOSTING:
        raise FleetPlanInvalid("Internal Plans use their owning Make/certification command")
    if reviewers and key != "agents/agentic-review-repair-loop":
        raise FleetPlanInvalid("Reviewer authoring requires agents/agentic-review-repair-loop")
    source = PlanTemplateStore().source(key)
    goons = _FrozenGoonSources(GoonTemplateStore())
    resolver = FleetPlanResolver(goon_templates=goons)
    graph = resolver.analyze_source(source.path, compiled_overrides={source.path: source.compiled})
    interface = inspect_projected_inputs(graph)
    payload: dict[str, Any] = {
        "version": 1,
        "source": {"kind": "catalog-plan", "name": key},
        "target": {"repository_id": "REPOSITORY_ID"},
    }
    required = {
        item.definition.name: f"REQUIRED_{item.definition.name}"
        for item in interface
        if item.definition.required and item.definition.default is None
    }
    agent_steps = [
        step
        for owner, identifier in graph.agent_step_sources()
        for step in graph.node(owner).compiled.steps
        if isinstance(step, AgentPlanStep) and step.step_id == identifier
    ]
    phases: set[str] = set()

    def collect_phases(owner: Path) -> None:
        for step in graph.node(owner).compiled.steps:
            if isinstance(step, CompiledGoonImportStep):
                if step.phase is not None:
                    phases.add(step.phase)
                collect_phases(graph.goon(owner, step).path)

    collect_phases(graph.root.path)
    if graph.has_agent_source_contract():
        required.pop("acceptance", None)
        acceptance = next(
            (item.definition.default for item in interface if item.definition.name == "acceptance"),
            None,
        )
        selection = (
            {"implementation": "pi", "profile": DEFAULT_NATIVE_AGENT_PROFILE}
            if any(step.harness == "native" for step in agent_steps)
            else {"model_profile": "REQUIRED_MODEL_PROFILE"}
        )
        payload["agent"] = {
            **selection,
            "acceptance": acceptance or "REQUIRED_ACCEPTANCE",
            "repository_root": "/approved/repositories",
            "skill_root": ".",
        }
    if required:
        payload["inputs"] = required
    if "repair" in phases:
        payload["repair"] = {
            "review_passes": 5,
            "write_roots": ["REQUIRED_WRITE_ROOT"],
            "timeout_seconds": 1800,
            "step_timeout_seconds": 300,
        }
    files: dict[str, str] = {}
    if reviewers:
        files = _reviewer_workflow_files(goons, reviewers)
        payload["source"] = {"kind": "file-plan", "path": "plan.yml"}
    rendered = "# Select the registered target and replace REQUIRED values before validation.\n"
    rendered += f"# Selected Plan source SHA-256: {source.sha256} (provenance, not a lock).\n"
    files["run.yml"] = rendered + yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)
    # Staging is generator-owned execution scratch, independent of the chosen
    # output path. Publication pins native directories before its first write.
    with tempfile.TemporaryDirectory(prefix="oh-goon-workflow-") as temporary:
        staging = Path(temporary)
        for relative, content in files.items():
            destination = staging / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            with prepared_text_file(destination, content) as prepared:
                replace_prepared_file(prepared, destination)
        if reviewers:
            resolver.analyze_source(staging / "plan.yml")
        publish_new_text_tree(selected, files, commit_last="run.yml")
    return selected / "run.yml"


def _reviewer_workflow_files(
    goons: _FrozenGoonSources, reviewers: Sequence[tuple[str, str]]
) -> dict[str, str]:
    """Replace authoring review edges; the original Composite owns all other phases."""
    from oh_goon.integrations.fleet_plan_compilation import parse_goon_template

    source = goons.source("agents/agentic-review-repair-loop")
    original = source.document.payload
    edges = original["goons"]
    prototype = next(edge for edge in edges if edge.get("phase") == "review")
    review = goons.source(prototype["import"])
    skill = next(item for item in review.compiled.inputs if item.name == "skill")
    aliases: set[str] = set()
    selected: list[dict[str, Any]] = []
    if len(reviewers) > 32:
        raise FleetPlanInvalid("Reviewer authoring permits at most 32 explicit occurrences")
    for alias, reference in reviewers:
        if not alias or alias in aliases:
            raise FleetPlanInvalid("Reviewer aliases must be nonempty and unique")
        aliases.add(alias)
        coerce_input(skill, reference, context="reviewer authoring")
        selected.append({**prototype, "as": alias, "with": {"skill": reference}})
    composite = {**original, "goons": selected + [e for e in edges if e.get("phase") != "review"]}
    content = yaml.safe_dump(composite, sort_keys=False, allow_unicode=True)
    parse_goon_template(
        content, source="authored reviewer Composite", expected_name="review-repair"
    )
    plan = {
        "version": 1,
        "execution": {"purpose": "verification"},
        "goons": [{"import": "agents/review-repair", "from": "goons"}],
    }
    return {
        "goons/agents/review-repair/goon.yml": content,
        "goons/agents/review-repair/README.md": (
            "# agents/review-repair\n\n"
            "Project-owned ordered reviewers and the canonical remaining repair phases.\n"
            "Edit ordinary imports here; Skill sources remain in .goonskills.yml.\n"
        ),
        "plan.yml": yaml.safe_dump(plan, sort_keys=False),
    }


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


def _values_yaml(values: Mapping[str, object], inspections: tuple[InputInspection, ...]) -> str:
    definitions = {i.definition.name: i.definition for i in inspections}
    if len(values) > 64 or set(values) - set(definitions):
        raise FleetPlanInvalid("UX-C unknown caller value or more than 64 caller names")
    typed: dict[str, PlanInputValue] = {}
    for name, value in values.items():
        if type(value) not in (str, bool, int, type(None)):
            raise FleetPlanInvalid(f"UX-C caller value {name!r} must be scalar")
        if isinstance(value, str) and "${{" in value:
            raise FleetPlanInvalid("UX-C caller values cannot contain expressions")
        typed[name] = coerce_input(
            definitions[name], cast(PlanInputValue, value), context="UX-C draft"
        )
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
    original = source.document.payload
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
    proposed_imports: list[CompiledGoonImportStep] = []
    for step in compiled.steps:
        if not isinstance(step, CompiledGoonImportStep):
            raise FleetPlanInvalid("Plan authoring requires only ordered Goon imports")
        proposed_imports.append(step)
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
    old = []
    for original_step in source.compiled.steps:
        if not isinstance(original_step, CompiledGoonImportStep):
            raise FleetPlanInvalid("Plan authoring requires only ordered Goon imports")
        old.append((original_step.step_id, original_step.goon))
    new = [(s.step_id, s.goon) for s in proposed_imports]
    imports = sorted({(s.goon, graph.goon(source.path, s).source_sha256) for s in proposed_imports})
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
