from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import ExitStack
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath
from types import MappingProxyType

from oh_goon.core.errors import FleetPlanInvalid, FleetPlanSourceAcquisitionRequired
from oh_goon.core.models.fleet_plan import (
    CommandPlanStep,
    CompiledExecutionTemplate,
    CompiledGoonImportStep,
    ExtensionPlanStep,
    PlanStep,
    PreCommitPlanStep,
)
from oh_goon.core.models.git_sources import LocalApplicationSource, LockedExecutionSource
from oh_goon.core.path_authority import ContainedPathSpace, canonical_path, navigation_path
from oh_goon.core.product_identity import stable_id
from oh_goon.engine.errors import OperationCancelledError
from oh_goon.integrations.fleet_plan_compilation import GoonTemplateProvider
from oh_goon.integrations.fleet_plan_source_snapshot import (
    FleetPlanSourceSnapshot,
    FleetPlanStepPosition,
    read_fleet_plan_source,
)
from oh_goon.integrations.python_goon_projects import (
    CapturedProject,
    capture_project_source,
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
    git_source: LockedExecutionSource | None = None
    git_path: str | None = None
    step_positions: Mapping[str, FleetPlanStepPosition] = field(default_factory=dict)
    value_positions: Mapping[str, FleetPlanStepPosition] = field(default_factory=dict)
    project: CapturedProject | None = None
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
    execution_sources: tuple[LockedExecutionSource, ...] = ()

    def projects(self) -> tuple[CapturedProject, ...]:
        return tuple(node.project for node in self._nodes.values() if node.project is not None)

    def immutable_source_closure_identity(self) -> str:
        """Digest the already-analyzed original Plan + every imported Goon.

        This is a provenance commitment, not Run admission. No file rereads or
        secondary YAML resolution may occur during capture of agent sources.
        """
        entries = []
        nodes = sorted(
            ((_source_owner_key(node), node) for node in self._nodes.values()),
            key=lambda item: item[0],
        )
        for owner, node in nodes:
            if node.source_sha256 is None:
                raise FleetPlanInvalid(
                    "Agent source closure requires immutable original Goon bytes"
                )
            entries.append(
                {
                    "owner": owner if node.git_source is not None else node.path.as_posix(),
                    "sha256": node.source_sha256,
                    "kind": node.package_kind or "root",
                    "name": node.package_name or node.path.name,
                    **(
                        {"project": node.project.identity.model_dump(mode="json")}
                        if node.project is not None
                        else {}
                    ),
                }
            )
        return stable_id(
            "agent-original-plan-graph-closure-v1",
            {"sources": entries},
        )

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
    loaded: dict[tuple[str, ...], FleetPlanSourceNode] = field(default_factory=dict)
    extensions: list[str] = field(default_factory=list)
    seen_extensions: set[str] = field(default_factory=set)
    visited: set[Path] = field(default_factory=set)
    uses_pre_commit: bool = False
    acquisitions: ExitStack = field(default_factory=ExitStack)
    locked_sources: tuple[LockedExecutionSource, ...] | None = ()
    acquire_sources: bool = False
    update_goons: bool = False
    update_applications: bool = False
    allow_source_network: bool = False
    acquired_git: dict[tuple[str, str], tuple[Path, str]] = field(default_factory=dict)
    selected_git_commits: dict[tuple[str, str], str] = field(default_factory=dict)
    execution_sources: dict[tuple[str, ...], LockedExecutionSource] = field(default_factory=dict)
    project_bytes: int = 0
    archive_bytes: int = 0
    cancel_requested: Callable[[], bool] | None = None
    retained_nodes: Mapping[tuple[str, ...], FleetPlanSourceNode] = field(default_factory=dict)


def analyze_fleet_plan_sources(
    plan_path: Path,
    *,
    containment_root: Path,
    compiled_overrides: Mapping[Path, CompiledExecutionTemplate] | None = None,
    root_snapshot: FleetPlanSourceSnapshot | None = None,
    goon_templates: GoonTemplateProvider | None = None,
    root_expected_name: str | None = None,
    locked_sources: tuple[LockedExecutionSource, ...] | None = (),
    acquire_sources: bool = False,
    update_goons: bool = False,
    update_applications: bool = False,
    allow_source_network: bool = False,
    plan_source: LockedExecutionSource | None = None,
    cancel_requested: Callable[[], bool] | None = None,
    retained_graph: FleetPlanSourceGraph | None = None,
) -> FleetPlanSourceGraph:
    """Read each root/Goon source once and validate the complete composition graph."""

    if cancel_requested is not None and cancel_requested():
        raise OperationCancelledError("Plan source capture cancelled")
    path = navigation_path(plan_path)
    pinned_commits: dict[tuple[str, str], str] = {}
    for pin in locked_sources or ():
        previous = pinned_commits.setdefault((pin.git, pin.ref), pin.commit)
        if previous != pin.commit:
            raise FleetPlanInvalid("Locked Git source selections disagree on their captured commit")
    selected_commits = {
        (pin.git, pin.ref): pin.commit
        for pin in locked_sources or ()
        if not (update_applications if pin.kind == "application" else update_goons)
    }
    if plan_source is not None:
        previous = selected_commits.setdefault(
            (plan_source.git, plan_source.ref), plan_source.commit
        )
        if previous != plan_source.commit:
            raise FleetPlanInvalid("Selected Git Plan differs from the locked source commit")
    overrides = {
        canonical_path(item): template for item, template in (compiled_overrides or {}).items()
    }
    root = _read_node(path, overrides, expected_name=root_expected_name, snapshot=root_snapshot)
    if plan_source is not None:
        assert plan_source.plan is not None
        logical = (PurePosixPath(plan_source.path) / plan_source.plan).as_posix()
        root = replace(
            root,
            git_source=plan_source,
            git_path=logical,
            local_source=_git_source_label(plan_source, logical),
        )
    build = _SourceGraphBuild(
        loaded={("path", str(canonical_path(root.path))): root},
        locked_sources=locked_sources,
        acquire_sources=acquire_sources,
        update_goons=update_goons,
        allow_source_network=allow_source_network,
        selected_git_commits=selected_commits,
        update_applications=update_applications,
        cancel_requested=cancel_requested,
        retained_nodes={_source_owner_key(node): node for node in retained_graph._nodes.values()}
        if retained_graph is not None
        else {},
    )
    with build.acquisitions:
        project = _project_for_node(
            root,
            authoring_root=containment_root,
            build=build,
        )
        root = replace(root, project=project)
        build.loaded[("path", str(canonical_path(root.path)))] = root
        _visit_source(
            root,
            source_stack=(root,),
            overrides=overrides,
            goon_templates=goon_templates,
            authoring_root=containment_root,
            authoring_source=plan_source,
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
        execution_sources=tuple(
            ([plan_source] if plan_source is not None else [])
            + list(build.execution_sources.values())
        ),
    )


def _visit_source(
    node: FleetPlanSourceNode,
    *,
    source_stack: tuple[FleetPlanSourceNode, ...],
    overrides: Mapping[Path, CompiledExecutionTemplate],
    goon_templates: GoonTemplateProvider | None,
    authoring_root: Path,
    authoring_source: LockedExecutionSource | None,
    build: _SourceGraphBuild,
) -> None:
    if build.cancel_requested is not None and build.cancel_requested():
        raise OperationCancelledError("Plan source capture cancelled")
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
                authoring_source=authoring_source,
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
            authoring_source=child.git_source,
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
    snapshot: FleetPlanSourceSnapshot | None = None,
) -> FleetPlanSourceNode:
    if snapshot is not None and canonical_path(snapshot.path) != canonical_path(path):
        raise FleetPlanInvalid("Captured Plan source differs from the selected Plan")
    override = overrides.get(canonical_path(path))
    if override is not None:
        if snapshot is not None:
            raise FleetPlanInvalid("Captured Plan source cannot be mixed with a compiled override")
        return FleetPlanSourceNode(path=path, compiled=override)
    source = snapshot or read_fleet_plan_source(
        path, kind="goon" if path.name == "goon.yml" else "plan", expected_name=expected_name
    )
    return FleetPlanSourceNode(
        path=path,
        compiled=source.compiled,
        source_sha256=source.sha256,
        step_positions=source.step_positions,
        value_positions=source.value_positions,
    )


def _load_goon_child(
    step: CompiledGoonImportStep,
    *,
    parent_path: Path,
    authoring_root: Path,
    authoring_source: LockedExecutionSource | None,
    overrides: Mapping[Path, CompiledExecutionTemplate],
    provider: GoonTemplateProvider | None,
    build: _SourceGraphBuild,
) -> FleetPlanSourceNode:
    key = step.goon
    local_source = None
    local_root = None
    cache_key: tuple[str, ...] = ("goon", key)
    remote_root = None
    git_source = None
    git_path = None
    if step.git_source is not None:
        from oh_goon.integrations.git_source_preparation import prepare_git_source

        selection = step.git_source
        if build.locked_sources is None:
            raise FleetPlanInvalid(
                "Git source pins are unavailable; restore goon.lock before acquisition"
            )
        if tuple(PurePosixPath(selection.path).parts[-2:]) != tuple(key.split("/")):
            raise FleetPlanInvalid("Git Goon package path must preserve its category/name identity")
        coordinates = (selection.git, selection.ref, selection.path)
        frozen = next(
            (
                item
                for item in build.locked_sources
                if item.kind == "goon" and (item.git, item.ref, item.path) == coordinates
            ),
            None,
        )
        if frozen is None and not build.acquire_sources:
            raise FleetPlanSourceAcquisitionRequired(
                "Git Goon source requires explicit acquisition with lock; "
                "inspect its contract first with 'oh-goon workflow inspect run.yml --acquire'"
            )
        source_key = (selection.git, selection.ref)
        selected_commit = _selected_git_commit(build, "goon", source_key)
        if source_key not in build.acquired_git:
            build.acquired_git[source_key] = build.acquisitions.enter_context(
                prepare_git_source(
                    selection.git,
                    selection.ref,
                    commit=selected_commit,
                    allow_network=build.acquire_sources or build.allow_source_network,
                    cancel_requested=build.cancel_requested,
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
        git_source = LockedExecutionSource(**selection.model_dump(), commit=commit)
        git_path = (PurePosixPath(selection.path) / "goon.yml").as_posix()
        local_source = _git_source_label(git_source, git_path)
        cache_key = _git_source_key(git_source, git_path)
        build.execution_sources[("goon", *coordinates)] = git_source
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
        if authoring_source is not None:
            prefix = authoring_source.path if authoring_source.kind == "plan" else "."
            git_source = authoring_source
            git_path = (PurePosixPath(prefix) / relative).as_posix()
            local_source = _git_source_label(git_source, git_path)
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
    node = FleetPlanSourceNode(
        path=source.path,
        compiled=compiled,
        source_sha256=source.sha256 if compiled is source.compiled else None,
        package_kind="goon",
        package_name=key,
        local_source=local_source,
        git_source=git_source,
        git_path=git_path,
        step_positions=source.step_positions,
        value_positions=source.value_positions,
        authoring_root=remote_root,
    )
    node = replace(
        node,
        project=_project_for_node(
            node,
            authoring_root=remote_root or authoring_root,
            build=build,
            catalog_package=True,
        ),
    )
    build.loaded[cache_key] = node
    return node


def _git_source_label(source: LockedExecutionSource, path: str) -> str:
    return f"git:{source.git}@{source.commit}:{path}"


def _git_source_key(source: LockedExecutionSource, path: str) -> tuple[str, ...]:
    """Native coordinates retain authority independently of their display label."""
    return ("git", source.git, source.ref, source.commit, path)


def _source_owner_key(node: FleetPlanSourceNode) -> tuple[str, ...]:
    if node.git_source is not None:
        if node.git_path is None:
            raise FleetPlanInvalid("Git source owner provenance is unavailable")
        return _git_source_key(node.git_source, node.git_path)
    return ("file", node.path.as_posix())


def _capture_project(
    path: Path,
    compiled: CompiledExecutionTemplate,
    sha256: str | None,
    *,
    authoring_root: Path,
    authoring_source: LockedExecutionSource | None,
    build: _SourceGraphBuild,
    catalog_package: bool = False,
    max_source_bytes: int = 64 * 1024 * 1024,
    max_archive_bytes: int = 96 * 1024 * 1024,
) -> CapturedProject | None:
    application = compiled.application
    if application is not None or compiled.runtime == "python":
        selected = path.parent
        provenance: LocalApplicationSource | LockedExecutionSource | None = None
        if isinstance(application, LocalApplicationSource):
            requested = Path(application.path)
            selected = (
                requested
                if requested.is_absolute()
                else ContainedPathSpace.from_root(authoring_root).resolve_from(
                    authoring_root,
                    application.path,
                    label="Application source",
                    kind="directory",
                    require_exists=True,
                )
            )
            selected = canonical_path(selected)
            if not selected.is_dir():
                raise FleetPlanInvalid("Application source requires its exact existing directory")
            if requested.is_absolute() or authoring_source is None:
                provenance = LocalApplicationSource(path=str(selected))
            else:
                prefix = authoring_source.path if authoring_source.kind == "plan" else "."
                logical_path = (PurePosixPath(prefix) / application.path).as_posix()
                coordinates = (authoring_source.git, authoring_source.ref, logical_path)
                frozen = next(
                    (
                        pin
                        for pin in build.locked_sources or ()
                        if pin.kind == "application" and (pin.git, pin.ref, pin.path) == coordinates
                    ),
                    None,
                )
                if frozen is None and not build.acquire_sources:
                    raise FleetPlanSourceAcquisitionRequired(
                        "Git application requires explicit acquisition with lock"
                    )
                selected_commit = _selected_git_commit(
                    build, "application", (authoring_source.git, authoring_source.ref)
                )
                if selected_commit is not None and selected_commit != authoring_source.commit:
                    raise FleetPlanInvalid("Git application differs from its authoring commit")
                if (
                    frozen is not None
                    and not build.update_applications
                    and frozen.commit != authoring_source.commit
                ):
                    raise FleetPlanInvalid("Git application differs from its locked commit")
                provenance = LockedExecutionSource(
                    kind="application",
                    git=authoring_source.git,
                    ref=authoring_source.ref,
                    path=logical_path,
                    commit=authoring_source.commit,
                )
                build.execution_sources[("application", *coordinates)] = provenance
        elif application is not None:
            from oh_goon.integrations.git_source_preparation import prepare_git_source

            if build.locked_sources is None:
                raise FleetPlanInvalid("Execution source pins are unavailable; restore goon.lock")
            coordinates = (application.git, application.ref, application.path)
            frozen = next(
                (
                    pin
                    for pin in build.locked_sources
                    if pin.kind == "application" and (pin.git, pin.ref, pin.path) == coordinates
                ),
                None,
            )
            if frozen is None and not build.acquire_sources:
                raise FleetPlanSourceAcquisitionRequired(
                    "Git application requires explicit acquisition with lock"
                )
            key = (application.git, application.ref)
            selected_commit = _selected_git_commit(build, "application", key)
            if key not in build.acquired_git:
                build.acquired_git[key] = build.acquisitions.enter_context(
                    prepare_git_source(
                        application.git,
                        application.ref,
                        commit=selected_commit,
                        allow_network=build.acquire_sources or build.allow_source_network,
                        cancel_requested=build.cancel_requested,
                    )
                )
            checkout, commit = build.acquired_git[key]
            build.selected_git_commits[key] = commit
            if frozen is not None and not build.update_applications and commit != frozen.commit:
                raise FleetPlanInvalid("Git application differs from its locked commit")
            selected = ContainedPathSpace.from_root(checkout).resolve_from(
                checkout,
                application.path,
                label="Git application source",
                kind="directory",
                require_exists=True,
            )
            provenance = LockedExecutionSource(
                **application.model_dump(), kind="application", commit=commit
            )
            build.execution_sources[("application", *coordinates)] = provenance
        uv = next(
            (
                step.uv
                for step in compiled.steps
                if isinstance(step, CommandPlanStep) and step.uv is not None
            ),
            None,
        )
        project = capture_project_source(
            selected,
            compiled,
            yaml_sha256=sha256,
            application=provenance,
            python_project=uv is not None,
            uv_project=uv.project if uv is not None else ".",
            max_source_bytes=min(max_source_bytes, MAX_GRAPH_PROJECT_BYTES - build.project_bytes),
            max_archive_bytes=min(max_archive_bytes, MAX_GRAPH_PROJECT_BYTES - build.archive_bytes),
        )
        build.project_bytes += project.identity.byte_count
        build.archive_bytes += len(project.archive)
        return project
    if catalog_package and (path.parent / "pyproject.toml").is_file():
        raise FleetPlanInvalid("A Python Goon package must select runtime: python")
    return None


def _project_for_node(
    node: FleetPlanSourceNode,
    *,
    authoring_root: Path,
    build: _SourceGraphBuild,
    catalog_package: bool = False,
) -> CapturedProject | None:
    previous = build.retained_nodes.get(_source_owner_key(node))
    if previous is not None and previous.project is not None:
        if (
            node.source_sha256 != previous.source_sha256
            or node.compiled.application != previous.compiled.application
        ):
            raise FleetPlanInvalid("Captured project workflow changed during candidate recapture")
        project = previous.project
        if (
            build.project_bytes + project.identity.byte_count > MAX_GRAPH_PROJECT_BYTES
            or build.archive_bytes + len(project.archive) > MAX_GRAPH_PROJECT_BYTES
        ):
            raise FleetPlanInvalid("Captured projects exceed source graph bounds")
        build.project_bytes += project.identity.byte_count
        build.archive_bytes += len(project.archive)
        provenance = project.identity.application
        if isinstance(provenance, LockedExecutionSource):
            build.execution_sources[
                ("application", provenance.git, provenance.ref, provenance.path)
            ] = provenance
        return project
    if build.retained_nodes and node.compiled.application is not None:
        raise FleetPlanInvalid("Candidate recapture cannot select a new application source")
    return _capture_project(
        node.path,
        node.compiled,
        node.source_sha256,
        authoring_root=authoring_root,
        authoring_source=node.git_source,
        build=build,
        catalog_package=catalog_package,
        max_source_bytes=min(64 * 1024 * 1024, MAX_GRAPH_PROJECT_BYTES - build.project_bytes),
        max_archive_bytes=min(96 * 1024 * 1024, MAX_GRAPH_PROJECT_BYTES - build.archive_bytes),
    )


def _selected_git_commit(
    build: _SourceGraphBuild, kind: str, source: tuple[str, str]
) -> str | None:
    refreshing = build.update_applications if kind == "application" else build.update_goons
    if refreshing and any(
        (pin.git, pin.ref) == source
        and not (build.update_applications if pin.kind == "application" else build.update_goons)
        for pin in build.locked_sources or ()
    ):
        raise FleetPlanInvalid(
            "Git source refresh conflicts with a retained source kind; explicitly refresh both selections"
        )
    return build.selected_git_commits.get(source)
