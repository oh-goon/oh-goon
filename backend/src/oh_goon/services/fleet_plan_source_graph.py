from __future__ import annotations

from collections.abc import Mapping
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from types import MappingProxyType

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.fleet_plan import (
    CompiledExecutionTemplate,
    CompiledGoonImportStep,
    ExtensionPlanStep,
    PlanStep,
    PreCommitPlanStep,
)
from oh_goon.core.models.git_sources import LockedGoonSource
from oh_goon.core.path_authority import ContainedPathSpace, canonical_path, navigation_path
from oh_goon.core.product_identity import stable_id
from oh_goon.integrations.fleet_plan_compilation import GoonTemplateProvider
from oh_goon.integrations.fleet_plan_source_snapshot import (
    FleetPlanStepPosition,
    read_fleet_plan_source,
)
from oh_goon.integrations.python_goon_projects import (
    CapturedPythonGoonProject,
    capture_python_goon_project,
)

MAX_GRAPH_PROJECT_BYTES = 128 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class FleetPlanSourceNode:
    path: Path
    compiled: CompiledExecutionTemplate
    source_sha256: str | None = None
    package_kind: str | None = None
    package_name: str | None = None
    local_source: str | None = None
    step_positions: Mapping[str, FleetPlanStepPosition] = field(default_factory=dict)
    value_positions: Mapping[str, FleetPlanStepPosition] = field(default_factory=dict)
    python_project: CapturedPythonGoonProject | None = None
    authoring_root: Path | None = None


@dataclass(frozen=True, slots=True)
class FleetPlanSourceGraph:
    """One immutable interpretation of the complete Plan/Goon authoring graph."""

    root: FleetPlanSourceNode
    containment_root: Path
    required_extensions: tuple[str, ...]
    uses_pre_commit: bool
    _goon_edges: Mapping[tuple[Path, str], FleetPlanSourceNode]
    _nodes: Mapping[Path, FleetPlanSourceNode]
    goon_sources: tuple[LockedGoonSource, ...] = ()

    def python_projects(self) -> tuple[CapturedPythonGoonProject, ...]:
        return tuple(
            node.python_project for node in self._nodes.values() if node.python_project is not None
        )

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
                    walk(self.goon(node.path, step))

        walk(self.root)
        return tuple(found)

    def validate_agent_selection(
        self,
        *,
        native_selected: bool,
        require_review_phases: bool | None = None,
        model_selected: bool | None = None,
    ) -> None:
        """Refuse source/selection conflicts before registry acquisition or model capture."""
        from oh_goon.core.agent_review_plan import validate_agent_phase_steps
        from oh_goon.core.models.fleet_plan import AgentPlanStep, AgentStepRequestedEffects

        steps: dict[str, PlanStep] = {}
        owners: dict[str, str] = {}
        agent_steps: list[AgentPlanStep] = []

        def walk(node: FleetPlanSourceNode, lineage: str, inherited: str | None) -> None:
            for step in node.compiled.steps:
                identifier = f"{lineage}.{step.step_id}" if lineage else step.step_id
                if isinstance(step, CompiledGoonImportStep):
                    if inherited is not None and step.phase is not None:
                        raise FleetPlanInvalid(
                            "Agent phases overlap; label an import or its children, not both"
                        )
                    walk(self.goon(node.path, step), identifier, step.phase or inherited)
                else:
                    steps[identifier] = step
                    if inherited is not None:
                        owners[identifier] = inherited
                    if isinstance(step, AgentPlanStep):
                        agent_steps.append(step)

        walk(self.root, "", None)
        if not agent_steps:
            raise FleetPlanInvalid("Agent selection requires authored Agent Steps")
        if any((step.harness == "native") != native_selected for step in agent_steps):
            raise FleetPlanInvalid(
                "Selected native/provider agent differs from the authored harness"
            )
        if (
            model_selected is False
            and not native_selected
            and any(
                step.harness != "local-search"
                or step.requested_effects != AgentStepRequestedEffects()
                for step in agent_steps
            )
        ):
            raise FleetPlanInvalid(
                "Source-only agent selection requires recipient-free read-only local-search; "
                "model harnesses require an explicit model_profile or native selection"
            )
        if require_review_phases is False and owners:
            raise FleetPlanInvalid(
                "Authored bounded repair phases require the repair selection group"
            )
        if require_review_phases:
            validate_agent_phase_steps(steps, owners)

    def node(self, path: Path) -> FleetPlanSourceNode:
        node = self._nodes.get(canonical_path(path))
        if node is None:
            raise FleetPlanInvalid(f"Resolved composition graph has no analyzed source {path}")
        return node

    def goon(self, parent_path: Path, step: CompiledGoonImportStep) -> FleetPlanSourceNode:
        node = self._goon_edges.get((canonical_path(parent_path), step.step_id))
        if node is None:  # pragma: no cover - resolver asks only for analyzed edges
            raise FleetPlanInvalid(
                f"Resolved composition graph has no analyzed Goon occurrence {step.step_id!r} from {parent_path}"
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
    acquisitions: ExitStack = field(default_factory=ExitStack)
    locked_goons: tuple[LockedGoonSource, ...] | None = ()
    acquire_goons: bool = False
    update_goons: bool = False
    allow_source_network: bool = False
    acquired_git: dict[tuple[str, str], tuple[Path, str]] = field(default_factory=dict)
    selected_git_commits: dict[tuple[str, str], str] = field(default_factory=dict)
    goon_sources: dict[tuple[str, str, str], LockedGoonSource] = field(default_factory=dict)
    project_bytes: int = 0
    archive_bytes: int = 0


def analyze_fleet_plan_sources(
    plan_path: Path,
    *,
    containment_root: Path,
    compiled_overrides: Mapping[Path, CompiledExecutionTemplate] | None = None,
    goon_templates: GoonTemplateProvider | None = None,
    root_expected_name: str | None = None,
    locked_goons: tuple[LockedGoonSource, ...] | None = (),
    acquire_goons: bool = False,
    update_goons: bool = False,
    allow_source_network: bool = False,
    plan_source: LockedGoonSource | None = None,
) -> FleetPlanSourceGraph:
    """Read each root/Goon source once and validate the complete composition graph."""

    path = navigation_path(plan_path)
    pinned_commits: dict[tuple[str, str], str] = {}
    for pin in locked_goons or ():
        previous = pinned_commits.setdefault((pin.git, pin.ref), pin.commit)
        if previous != pin.commit:
            raise FleetPlanInvalid("Locked Git source selections disagree on their captured commit")
    selected_commits = {} if update_goons else pinned_commits
    if plan_source is not None:
        previous = selected_commits.setdefault(
            (plan_source.git, plan_source.ref), plan_source.commit
        )
        if previous != plan_source.commit:
            raise FleetPlanInvalid("Selected Git Plan differs from the locked source commit")
    overrides = {
        canonical_path(item): template for item, template in (compiled_overrides or {}).items()
    }
    root = _read_node(path, overrides, expected_name=root_expected_name)
    build = _SourceGraphBuild(
        loaded={("path", str(canonical_path(root.path))): root},
        locked_goons=locked_goons,
        acquire_goons=acquire_goons,
        update_goons=update_goons,
        allow_source_network=allow_source_network,
        selected_git_commits=selected_commits,
        project_bytes=root.python_project.identity.byte_count if root.python_project else 0,
        archive_bytes=len(root.python_project.archive) if root.python_project else 0,
    )
    with build.acquisitions:
        _visit_source(
            root,
            source_stack=(root,),
            overrides=overrides,
            goon_templates=goon_templates,
            authoring_root=containment_root,
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
        goon_sources=tuple(
            ([plan_source] if plan_source is not None else []) + list(build.goon_sources.values())
        ),
    )


def _visit_source(
    node: FleetPlanSourceNode,
    *,
    source_stack: tuple[FleetPlanSourceNode, ...],
    overrides: Mapping[Path, CompiledExecutionTemplate],
    goon_templates: GoonTemplateProvider | None,
    authoring_root: Path,
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
        key = (identity, step.step_id)
        child = build.goon_edges.get(key)
        if child is None:
            child = _load_goon_child(
                step,
                parent_path=node.path,
                authoring_root=authoring_root,
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
            authoring_root=child.authoring_root or authoring_root,
            build=build,
        )


def _record_runtime_requirement(step: PlanStep, build: _SourceGraphBuild) -> None:
    if isinstance(step, ExtensionPlanStep):
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
        value_positions=source.value_positions,
        python_project=_capture_project(source.path, source.compiled, source.sha256),
    )


def _load_goon_child(
    step: CompiledGoonImportStep,
    *,
    parent_path: Path,
    authoring_root: Path,
    overrides: Mapping[Path, CompiledExecutionTemplate],
    provider: GoonTemplateProvider | None,
    build: _SourceGraphBuild,
) -> FleetPlanSourceNode:
    key = step.goon
    local_source = None
    local_root = None
    cache_key = ("goon", key)
    remote_root = None
    if step.git_source is not None:
        from oh_goon.integrations.git_source_preparation import prepare_git_source

        selection = step.git_source
        if build.locked_goons is None:
            raise FleetPlanInvalid(
                "Git source pins are unavailable; restore goon.lock before acquisition"
            )
        if tuple(PurePosixPath(selection.path).parts[-2:]) != tuple(key.split("/")):
            raise FleetPlanInvalid("Git Goon package path must preserve its category/name identity")
        coordinates = (selection.git, selection.ref, selection.path)
        frozen = next(
            (
                item
                for item in build.locked_goons
                if item.kind == "goon" and (item.git, item.ref, item.path) == coordinates
            ),
            None,
        )
        if frozen is None and not build.acquire_goons:
            raise FleetPlanInvalid("Git Goon source requires explicit acquisition with lock")
        source_key = (selection.git, selection.ref)
        if source_key not in build.acquired_git:
            build.acquired_git[source_key] = build.acquisitions.enter_context(
                prepare_git_source(
                    selection.git,
                    selection.ref,
                    commit=build.selected_git_commits.get(source_key),
                    allow_network=build.acquire_goons or build.allow_source_network,
                )
            )
        checkout, commit = build.acquired_git[source_key]
        build.selected_git_commits[source_key] = commit
        if frozen is not None and not build.update_goons and commit != frozen.commit:
            raise FleetPlanInvalid("Git Goon selections disagree on the locked commit")
        selected = ContainedPathSpace.from_root(checkout).resolve_from(
            checkout,
            selection.path,
            label="Git Goon package",
            kind="directory",
            require_exists=True,
        )
        remote_root = checkout
        local_root = selected.parent.parent
        local_source = f"git:{selection.git}@{commit}:{selection.path}/goon.yml"
        cache_key = ("git", local_source)
        build.goon_sources[coordinates] = LockedGoonSource(**selection.model_dump(), commit=commit)
    elif step.source_root is not None:
        space = ContainedPathSpace.from_root(authoring_root)
        root = space.resolve_from(
            parent_path.parent,
            step.source_root,
            label="Local Goon catalog",
            kind="directory",
            require_exists=True,
        )
        selected = root / key / "goon.yml"
        relative = space.relative(selected, label="Local Goon source")
        local_source = f"local:{relative.as_posix()}"
        cache_key = ("path", str(canonical_path(selected)))
        local_root = root
    cached = build.loaded.get(cache_key)
    if cached is not None:
        return cached
    if provider is None and local_root is None:
        raise FleetPlanInvalid(
            f"Plan or Composite Goon imports Goon {key!r}, but no Goon template store is configured"
        )
    try:
        if local_root is not None:
            from oh_goon.runtime.template_discovery import template_package

            _, directory = template_package(local_root, key, source_name="goon.yml", kind="Goon")
            source = read_fleet_plan_source(
                directory / "goon.yml", kind="goon", expected_name=directory.name
            )
        else:
            assert provider is not None
            source = provider.source(key)
    except FleetPlanInvalid as exc:
        raise FleetPlanInvalid(f"Unknown Goon import {key!r}: {exc}") from exc
    compiled = overrides.get(canonical_path(source.path), source.compiled)
    if compiled.execution is not None:
        raise FleetPlanInvalid(
            f"Imported Goon {key!r} cannot declare execution policy; execution belongs to the root Plan or repository Goon"
        )
    captured_project = _capture_project(
        source.path,
        compiled,
        source.sha256,
        catalog_package=True,
        max_source_bytes=min(64 * 1024 * 1024, MAX_GRAPH_PROJECT_BYTES - build.project_bytes),
        max_archive_bytes=min(96 * 1024 * 1024, MAX_GRAPH_PROJECT_BYTES - build.archive_bytes),
    )
    if captured_project is not None:
        build.project_bytes += captured_project.identity.byte_count
        build.archive_bytes += len(captured_project.archive)
    node = FleetPlanSourceNode(
        path=source.path,
        compiled=compiled,
        source_sha256=source.sha256 if compiled is source.compiled else None,
        package_kind="goon",
        package_name=key,
        local_source=local_source,
        step_positions=source.step_positions,
        value_positions=source.value_positions,
        python_project=captured_project,
        authoring_root=remote_root,
    )
    build.loaded[cache_key] = node
    return node


def _capture_project(
    path: Path,
    compiled: CompiledExecutionTemplate,
    sha256: str,
    *,
    catalog_package: bool = False,
    max_source_bytes: int = 64 * 1024 * 1024,
    max_archive_bytes: int = 96 * 1024 * 1024,
) -> CapturedPythonGoonProject | None:
    if compiled.runtime == "python":
        return capture_python_goon_project(
            path.parent,
            compiled,
            yaml_sha256=sha256,
            max_source_bytes=max_source_bytes,
            max_archive_bytes=max_archive_bytes,
        )
    if catalog_package and (path.parent / "pyproject.toml").is_file():
        raise FleetPlanInvalid("A Python Goon package must select runtime: python")
    return None
