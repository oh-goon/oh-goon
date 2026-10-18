from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.fleet_plan import (
    CompiledExecutionTemplate,
    CompiledGoonImportStep,
    PreCommitPlanStep,
)
from oh_goon.core.path_authority import canonical_path, navigation_path
from oh_goon.core.product_identity import stable_id
from oh_goon.integrations.fleet_plan_compilation import GoonTemplateProvider
from oh_goon.integrations.fleet_plan_source_snapshot import (
    FleetPlanStepPosition,
    read_fleet_plan_source,
)


@dataclass(frozen=True, slots=True)
class FleetPlanSourceNode:
    path: Path
    compiled: CompiledExecutionTemplate
    source_sha256: str | None = None
    package_kind: str | None = None
    package_name: str | None = None
    step_positions: Mapping[str, FleetPlanStepPosition] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class FleetPlanSourceGraph:
    """One immutable interpretation of the complete Plan/Goon authoring graph."""

    root: FleetPlanSourceNode
    containment_root: Path
    required_extensions: tuple[str, ...]
    uses_pre_commit: bool
    _goon_edges: Mapping[tuple[Path, str], FleetPlanSourceNode]
    _nodes: Mapping[Path, FleetPlanSourceNode]

    def immutable_source_closure_identity(self) -> str:
        """Digest the already-analyzed original Plan + every imported Goon.

        This is a provenance commitment, not Run admission. No file rereads or
        secondary YAML resolution may occur during capture of agent sources.
        """
        entries = []
        for path, node in sorted(self._nodes.items(), key=lambda pair: pair[0].as_posix()):
            if node.source_sha256 is None:
                raise FleetPlanInvalid(
                    "Agent source closure requires immutable original Goon bytes"
                )
            entries.append(
                {
                    "owner": path.as_posix(),
                    "sha256": node.source_sha256,
                    "kind": node.package_kind or "root",
                    "name": node.package_name or node.path.name,
                }
            )
        return stable_id("agent-original-plan-graph-closure-v1", {"sources": entries})

    def has_agent_source_contract(self) -> bool:
        """Discover preview-only agent source inputs in every analyzed Goon.

        The original compiler's graph is the only owner of this census; a
        non-projected child input must never become a hidden execution bypass.
        """
        from oh_goon.core.models.fleet_plan_composition import PlanInputType

        return any(
            definition.input_type
            in {
                PlanInputType.REPOSITORY_REFERENCE,
                PlanInputType.SKILL_REFERENCE,
                PlanInputType.ACCEPTANCE_PROFILE,
            }
            for node in self._nodes.values()
            for definition in node.compiled.inputs
        )

    def agent_step_sources(self) -> tuple[tuple[Path, str], ...]:
        """Count *occurrences* through original ordered imports, not unique nodes.

        Importing an identical Atomic Goon twice must not silently bind a
        prework handoff to two potential executions. The original source graph
        already owns cycle checks, import edges and template meaning.
        """
        from oh_goon.core.models.fleet_plan import AgentPlanStep, CompiledGoonImportStep

        found: list[tuple[Path, str]] = []

        def walk(node: FleetPlanSourceNode) -> None:
            for step in node.compiled.steps:
                if isinstance(step, AgentPlanStep):
                    found.append((node.path, step.step_id))
                elif isinstance(step, CompiledGoonImportStep):
                    walk(self.goon(node.path, step.goon))

        walk(self.root)
        return tuple(found)

    def node(self, path: Path) -> FleetPlanSourceNode:
        node = self._nodes.get(canonical_path(path))
        if node is None:
            raise FleetPlanInvalid(f"Resolved composition graph has no analyzed source {path}")
        return node

    def goon(self, parent_path: Path, reference: str) -> FleetPlanSourceNode:
        node = self._goon_edges.get((canonical_path(parent_path), reference))
        if node is None:  # pragma: no cover - resolver asks only for analyzed edges
            raise FleetPlanInvalid(
                f"Resolved composition graph has no analyzed Goon import {reference!r} from {parent_path}"
            )
        return node


@dataclass(slots=True)
class _SourceGraphBuild:
    goon_edges: dict[tuple[Path, str], FleetPlanSourceNode] = field(default_factory=dict)
    loaded: dict[tuple[str, str], FleetPlanSourceNode] = field(default_factory=dict)
    extensions: list[str] = field(default_factory=list)
    seen_extensions: set[str] = field(default_factory=set)
    visited: set[Path] = field(default_factory=set)
    uses_pre_commit: bool = False


def analyze_fleet_plan_sources(
    plan_path: Path,
    *,
    containment_root: Path,
    compiled_overrides: Mapping[Path, CompiledExecutionTemplate] | None = None,
    goon_templates: GoonTemplateProvider | None = None,
    root_expected_name: str | None = None,
) -> FleetPlanSourceGraph:
    """Read each root/Goon source once and validate the complete composition graph."""

    path = navigation_path(plan_path)
    overrides = {
        canonical_path(item): template for item, template in (compiled_overrides or {}).items()
    }
    root = _read_node(path, overrides, expected_name=root_expected_name)
    build = _SourceGraphBuild(loaded={("path", str(canonical_path(root.path))): root})
    _visit_source(
        root,
        source_stack=(root,),
        overrides=overrides,
        goon_templates=goon_templates,
        build=build,
    )
    return FleetPlanSourceGraph(
        root=root,
        containment_root=containment_root,
        required_extensions=tuple(build.extensions),
        uses_pre_commit=build.uses_pre_commit,
        _goon_edges=MappingProxyType(build.goon_edges),
        _nodes=MappingProxyType(
            {canonical_path(node.path): node for node in build.loaded.values()}
        ),
    )


def _visit_source(
    node: FleetPlanSourceNode,
    *,
    source_stack: tuple[FleetPlanSourceNode, ...],
    overrides: Mapping[Path, CompiledExecutionTemplate],
    goon_templates: GoonTemplateProvider | None,
    build: _SourceGraphBuild,
) -> None:
    identity = canonical_path(node.path)
    if identity in build.visited:
        return
    build.visited.add(identity)
    for step in node.compiled.steps:
        _record_runtime_requirement(step, build)
        if not isinstance(step, CompiledGoonImportStep):
            continue
        key = (identity, step.goon)
        child = build.goon_edges.get(key)
        if child is None:
            child = _load_goon_child(
                step.goon,
                overrides=overrides,
                provider=goon_templates,
                build=build,
            )
            build.goon_edges[key] = child
        child_name = child.package_name
        if child_name is None:  # pragma: no cover - only Goon edges are valid
            raise FleetPlanInvalid("Composition edge did not resolve to a Goon")
        paths = [canonical_path(item.path) for item in source_stack]
        child_path = canonical_path(child.path)
        if child_path in paths:
            start = paths.index(child_path)
            cycle_nodes = source_stack[start:]
            labels = [item.package_name or child_name for item in cycle_nodes]
            raise FleetPlanInvalid(
                "Invalid Goon composition cycle: " + " -> ".join((*labels, child_name))
            )
        _visit_source(
            child,
            source_stack=(*source_stack, child),
            overrides=overrides,
            goon_templates=goon_templates,
            build=build,
        )


def _record_runtime_requirement(step, build: _SourceGraphBuild) -> None:
    if getattr(step, "kind", None) == "extension":
        name = step.extension
        if name not in build.seen_extensions:
            build.seen_extensions.add(name)
            build.extensions.append(name)
    if isinstance(step, PreCommitPlanStep):
        build.uses_pre_commit = True


def _read_node(
    path: Path,
    overrides: Mapping[Path, CompiledExecutionTemplate],
    *,
    expected_name: str | None = None,
) -> FleetPlanSourceNode:
    override = overrides.get(canonical_path(path))
    if override is not None:
        return FleetPlanSourceNode(path=path, compiled=override)
    source = read_fleet_plan_source(
        path, kind="goon" if path.name == "goon.yml" else "plan", expected_name=expected_name
    )
    return FleetPlanSourceNode(
        path=path,
        compiled=source.compiled,
        source_sha256=source.sha256,
        step_positions=source.step_positions,
    )


def _load_goon_child(
    reference: str,
    *,
    overrides: Mapping[Path, CompiledExecutionTemplate],
    provider: GoonTemplateProvider | None,
    build: _SourceGraphBuild,
) -> FleetPlanSourceNode:
    key = reference
    cached = build.loaded.get(("goon", key))
    if cached is not None:
        return cached
    if provider is None:
        raise FleetPlanInvalid(
            f"Plan or Composite Goon imports Goon {key!r}, but no Goon template store is configured"
        )
    try:
        source = provider.source(key)
    except FleetPlanInvalid as exc:
        raise FleetPlanInvalid(f"Unknown Goon import {key!r}: {exc}") from exc
    compiled = overrides.get(canonical_path(source.path), source.compiled)
    if compiled.execution is not None:
        raise FleetPlanInvalid(
            f"Imported Goon {key!r} cannot declare execution policy; execution belongs to the root Plan or repository Goon"
        )
    node = FleetPlanSourceNode(
        source.path,
        compiled,
        source.sha256 if compiled is source.compiled else None,
        "goon",
        key,
        source.step_positions,
    )
    build.loaded[("goon", key)] = node
    return node
