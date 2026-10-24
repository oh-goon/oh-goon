from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from contextlib import ExitStack, nullcontext
from dataclasses import replace
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import ValidationError

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.fleet_plan_composition import bind_inputs, validate_condition_dependencies
from oh_goon.core.models.agent_providers import ResolvedNativeAgent
from oh_goon.core.models.execution_lock_models import LockedSkillSource
from oh_goon.core.models.fleet_plan import (
    AgentPlanStep,
    CompiledExecutionTemplate,
    ResolvedCommandStep,
    ResolvedFleetPlan,
    ResolvedLocalSearchAgentStep,
    ResolvedPlanStep,
)
from oh_goon.core.models.fleet_plan_composition import PlanInputDefinition, PlanInputValue
from oh_goon.core.models.fleet_plan_diagnostics import (
    FleetPlanCheckReport,
    FleetPlanResolutionReport,
    FleetPlanSemanticDiff,
    PlanCheckIssue,
    PlanResolutionEntry,
    PlanValueResolutionEntry,
)
from oh_goon.core.models.fleet_plan_evidence import FleetPlanPreview
from oh_goon.core.models.fleet_plan_step_contract import (
    PlanStepCache,
)
from oh_goon.core.path_authority import RepositoryPathSpace, navigation_path
from oh_goon.engine.errors import OperationCancelledError
from oh_goon.engine.fleet_extensions import FleetExtensionStore
from oh_goon.engine.fleet_tools import FleetToolStore
from oh_goon.engine.git import GitService
from oh_goon.integrations.fleet_plan_compilation import (
    GoonTemplateProvider,
    load_extension_catalog,
)
from oh_goon.integrations.fleet_plan_preview import preview_plan
from oh_goon.services.fleet_plan_compiled_resolution import resolve_compiled_steps
from oh_goon.services.fleet_plan_input_inspection import (
    InputInspection,
    InputInterfaceInspection,
    inspect_input_interface,
)
from oh_goon.services.fleet_plan_input_resolution import (
    InputResolutionContext,
    materialize_projected_inputs,
)
from oh_goon.services.fleet_plan_input_routing import CallerFileRoutingOrigin
from oh_goon.services.fleet_plan_resolution_context import FleetPlanResolutionContext
from oh_goon.services.fleet_plan_resolution_diagnostics import FleetPlanResolutionDiagnostics
from oh_goon.services.fleet_plan_source_graph import (
    FleetPlanSourceGraph,
    analyze_fleet_plan_sources,
)

if TYPE_CHECKING:
    from oh_goon.core.models.agent_source import (
        FrozenAgentSourceCapture,
        FrozenAgentStepHandoff,
        FrozenSourceTree,
    )
    from oh_goon.services.fleet_plan_resolution_context import (
        AgentCapture,
        AgentConnectionSelection,
    )


class FleetPlanResolver:
    """Resolve declarative authoring into the one immutable Fleet execution plan."""

    def __init__(
        self,
        extensions: FleetExtensionStore | None = None,
        tools: FleetToolStore | None = None,
        goon_templates: GoonTemplateProvider | None = None,
        *,
        git_service: GitService | None = None,
    ) -> None:
        self._extensions = extensions
        self._tools = tools
        self.goon_templates = goon_templates
        self._git_service = git_service or GitService()

    def analyze_source(
        self,
        plan_path: Path,
        *,
        containment_root: Path | None = None,
        compiled_overrides: Mapping[Path, CompiledExecutionTemplate] | None = None,
        root_expected_name: str | None = None,
    ) -> FleetPlanSourceGraph:
        path = navigation_path(plan_path)
        root = _containment_root(path, containment_root)
        return analyze_fleet_plan_sources(
            path,
            containment_root=root,
            compiled_overrides=compiled_overrides,
            goon_templates=self.goon_templates,
            root_expected_name=root_expected_name,
        )

    def resolve_source_graph(
        self,
        source_graph: FleetPlanSourceGraph,
        *,
        extensions_path: Path | None = None,
        inputs: dict[str, PlanInputValue] | None = None,
        repository: Path | None = None,
        environment: Mapping[str, str] | None = None,
        resolved_inputs: dict[str, PlanInputValue] | None = None,
        observe_environment: bool = True,
    ) -> ResolvedFleetPlan:
        plan, _, _ = self._resolve_graph(
            source_graph,
            extensions_path=extensions_path,
            inputs=inputs,
            trace=None,
            repository=repository,
            environment=environment,
            resolved_inputs=resolved_inputs,
            observe_environment=observe_environment,
        )
        return plan

    def resolve_frozen_agents(
        self,
        plan_path: Path,
        *,
        inputs: dict[str, PlanInputValue],
        repository_root: Path,
        skill_root: Path,
        allowed_acceptance: tuple[str, ...],
        extensions_path: Path | None = None,
        cancel_requested: Callable[[], bool] | None = None,
        previous: dict[str, "FrozenAgentStepHandoff"] | None = None,
        connection: AgentConnectionSelection | None = None,
        locked_skills: tuple[LockedSkillSource, ...] | None = None,
        update_skills: bool = False,
        require_review_phases: bool | None = None,
        source_graph: FleetPlanSourceGraph | None = None,
        native_agent: ResolvedNativeAgent | None = None,
        repository: Path | None = None,
        git_service: GitService | None = None,
    ) -> tuple[ResolvedFleetPlan, dict[str, FrozenAgentStepHandoff], str]:
        """Freeze each occurrence while the canonical compiler binds its inputs.

        One repository capture is shared by every delivery in this candidate.
        Repeated imports keep their occurrence identities and may select different Skills.
        """
        from oh_goon.core.models.agent_source import FrozenAgentStepHandoff
        from oh_goon.services.agent_authoring_sources import capture_agent_sources
        from oh_goon.services.agent_skill_registry import (
            FrozenSkillRegistry,
            original_skill_aliases,
        )
        from oh_goon.services.fleet_plan_step_resolution import require_agent_step_handoff

        if native_agent is not None and repository is None:
            raise FleetPlanInvalid(
                "Native Agent capture requires its explicit repository workspace"
            )
        private_native_roots = (
            (Path(native_agent.profile.codex_service.home),)
            if native_agent is not None and native_agent.profile.codex_service is not None
            else ()
        )
        graph = source_graph if source_graph is not None else self.analyze_source(plan_path)
        if graph.root.path != navigation_path(plan_path):
            raise FleetPlanInvalid("Captured source graph differs from the selected Plan")
        graph.validate_agent_selection(
            native_selected=native_agent is not None, require_review_phases=require_review_phases
        )
        source_sha = graph.root.source_sha256
        if source_sha is None:
            raise FleetPlanInvalid("Agent Plan source fingerprint is unavailable")
        graph_identity = graph.immutable_source_closure_identity()
        with ExitStack() as owned_sources:
            aliases: Mapping[str, str] | None = None
            aliases_loaded = False
            if callable(connection):
                connection = cache(connection)
            deliveries: dict[str, FrozenAgentStepHandoff] = {}
            trees: dict[tuple[str, str], FrozenSourceTree] = {}
            repository_reference: str | None = None

            def capture(
                owner: Path,
                step: AgentPlanStep,
                occurrence: str,
                definitions: tuple[PlanInputDefinition, ...],
                values: dict[str, PlanInputValue],
            ) -> FrozenAgentStepHandoff:
                nonlocal repository_reference, aliases, aliases_loaded
                if cancel_requested is not None and cancel_requested():
                    raise OperationCancelledError("Agent source capture cancelled")
                old = previous.get(occurrence) if previous is not None else None
                if previous is not None and old is None:
                    raise FleetPlanInvalid("Agent continuation changed its occurrence membership")
                if old is not None:
                    if (
                        old.source.plan_source_sha256 != source_sha
                        or old.source.plan_graph_identity != graph_identity
                    ):
                        raise FleetPlanInvalid("Agent continuation changed Plan/Goon sources")
                    require_agent_step_handoff(
                        step, old, owner_source_sha256=graph.node(owner).source_sha256
                    )
                if not aliases_loaded:
                    # Root and child bindings have already passed through the
                    # canonical resolver before source acquisition is possible.
                    aliases = owned_sources.enter_context(
                        original_skill_aliases(
                            skill_root,
                            None,
                            locked_sources=locked_skills,
                            update_sources=update_skills,
                            cancel_requested=cancel_requested,
                            native_workspace=repository if native_agent is not None else None,
                        )
                    )
                    aliases_loaded = True
                source = capture_agent_sources(
                    definitions,
                    values,
                    repository_root=repository_root,
                    skill_root=skill_root,
                    allowed_acceptance=allowed_acceptance,
                    plan_source_sha256=source_sha,
                    plan_graph_identity=graph_identity,
                    skill_aliases=aliases,
                    previous_source=old.source if old else None,
                    expected_repository=repository if native_agent is not None else None,
                    cancel_requested=cancel_requested,
                    tree_cache=trees,
                    private_native_roots=private_native_roots,
                )
                if (
                    repository_reference is not None
                    and source.repository_reference != repository_reference
                ):
                    raise FleetPlanInvalid("Agent occurrences must share one candidate repository")
                repository_reference = source.repository_reference
                handoff = FrozenAgentStepHandoff(
                    source=source,
                    owner_source_sha256=graph.node(owner).source_sha256 or "",
                    step_id=step.step_id,
                    harness=step.harness,
                    task=step.task,
                    timeout_seconds=step.timeout_seconds,
                    requested_effects=step.requested_effects,
                )
                handoff.verify()
                deliveries[occurrence] = handoff
                return handoff

            plan, _, _ = self._resolve_graph(
                graph,
                extensions_path=extensions_path,
                inputs=inputs,
                trace=None,
                agent_capture=capture,
                agent_connection=connection,
                native_agent=native_agent,
                repository=repository,
                input_git_service=git_service,
                skill_sources=lambda: aliases.pins
                if isinstance(aliases, FrozenSkillRegistry)
                else (),
            )
            if not deliveries or (previous is not None and deliveries.keys() != previous.keys()):
                raise FleetPlanInvalid("Agent delivery membership is incomplete")
            return plan, deliveries, source_sha

    def resolve_frozen_local_search_agent(
        self,
        plan_path: Path,
        *,
        inputs: dict[str, PlanInputValue],
        repository_root: Path,
        skill_root: Path,
        allowed_acceptance: tuple[str, ...],
        extensions_path: Path | None = None,
        handoff: "FrozenAgentStepHandoff | None" = None,
        cancel_requested: Callable[[], bool] | None = None,
        locked_skills: tuple[LockedSkillSource, ...] | None = None,
        source_graph: FleetPlanSourceGraph | None = None,
    ) -> tuple[ResolvedFleetPlan, "FrozenAgentStepHandoff", str]:
        """One original source graph, immutable packet, original typed pipeline.

        This is pre-admission intent only. The normal Fleet authority still
        decides whether this operator-selected workload can execute.
        """
        from oh_goon.core.models.agent_source import FrozenAgentStepHandoff
        from oh_goon.core.models.fleet_plan import AgentPlanStep

        if cancel_requested is not None and cancel_requested():
            raise OperationCancelledError("Agent source capture cancelled")
        graph = source_graph if source_graph is not None else self.analyze_source(plan_path)
        if graph.root.path != navigation_path(plan_path):
            raise FleetPlanInvalid("Captured source graph belongs to a different selected source")
        matches = graph.agent_step_sources()
        if len(matches) != 1 or graph.root.source_sha256 is None:
            raise FleetPlanInvalid("standard-local Agent requires exactly one original Agent Step")
        owner, raw_id = matches[0]
        authored = next(
            (
                item
                for item in graph.node(owner).compiled.steps
                if isinstance(item, AgentPlanStep) and item.step_id == raw_id
            ),
            None,
        )
        if authored is None:
            raise FleetPlanInvalid("original Agent Step source owner is missing")
        from oh_goon.services.agent_skill_registry import (
            FrozenSkillRegistry,
            original_skill_aliases,
        )

        aliases_context = (
            nullcontext(None)
            if handoff is not None
            else original_skill_aliases(
                skill_root,
                None,
                locked_sources=locked_skills,
                cancel_requested=cancel_requested,
            )
        )
        with aliases_context as aliases:
            if handoff is None:
                source = self._capture_agent_source_graph(
                    graph,
                    inputs=inputs,
                    repository_root=repository_root,
                    skill_root=skill_root,
                    allowed_acceptance=allowed_acceptance,
                    expected_source_sha256=graph.root.source_sha256,
                    skill_aliases=aliases,
                    cancel_requested=cancel_requested,
                )
                handoff = FrozenAgentStepHandoff(
                    source=source,
                    owner_source_sha256=graph.node(owner).source_sha256 or "",
                    step_id=raw_id,
                    harness=authored.harness,
                    task=authored.task,
                    timeout_seconds=authored.timeout_seconds,
                    requested_effects=authored.requested_effects,
                )
            elif type(handoff) is not FrozenAgentStepHandoff:
                raise FleetPlanInvalid("Agent source requires an original immutable handoff")
            handoff.verify()
            if (
                handoff.source.resolved_skill_reference.startswith("git:")
                and not locked_skills
                and aliases is None
            ):
                raise FleetPlanInvalid(
                    "Remote Skill handoff requires its resolved Skill source pins"
                )
            if handoff.source.acceptance_profile not in allowed_acceptance or len(
                set(allowed_acceptance)
            ) != len(allowed_acceptance):
                raise FleetPlanInvalid("Agent acceptance profile is not explicitly authorized")
            plan, _, _ = self._resolve_graph(
                graph,
                extensions_path=extensions_path,
                inputs=inputs,
                trace=None,
                agent_handoff=handoff,
                agent_owner_path=owner,
                skill_sources=aliases.pins
                if isinstance(aliases, FrozenSkillRegistry)
                else (locked_skills or ()),
            )
            resolved = [
                item for item in plan.steps if isinstance(item, ResolvedLocalSearchAgentStep)
            ]
            if len(resolved) != 1:
                raise FleetPlanInvalid("Agent Step did not resolve through the original pipeline")
            return plan, handoff, graph.root.source_sha256

    def recapture_agent_step_handoff(
        self,
        plan_path: Path,
        *,
        previous_handoff: "FrozenAgentStepHandoff",
        expected_previous_handoff_identity: str,
        inputs: dict[str, PlanInputValue],
        repository_root: Path,
        skill_root: Path,
        allowed_acceptance: tuple[str, ...],
        cancel_requested: Callable[[], bool] | None = None,
        locked_skills: tuple[LockedSkillSource, ...] | None = None,
    ) -> "FrozenAgentStepHandoff":
        """Explicitly capture a repaired repository for a separately admitted attempt.

        The previous delivery remains immutable. Plan, Step, source selection and
        Skill changes require a new authorization rather than repair continuation.
        This creates prework only; Fleet still owns admission and cancellation.
        """
        from oh_goon.core.models.agent_source import FrozenAgentStepHandoff
        from oh_goon.core.models.fleet_plan import AgentPlanStep
        from oh_goon.services.fleet_plan_step_resolution import require_agent_step_handoff

        if cancel_requested is not None and cancel_requested():
            raise OperationCancelledError("Agent repair capture cancelled")
        if (
            type(previous_handoff) is not FrozenAgentStepHandoff
            or type(expected_previous_handoff_identity) is not str
            or previous_handoff.handoff_identity != expected_previous_handoff_identity
        ):
            raise FleetPlanInvalid("Agent repair previous handoff identity mismatch")
        graph = self.analyze_source(plan_path)
        if (
            graph.root.source_sha256 != previous_handoff.source.plan_source_sha256
            or graph.immutable_source_closure_identity()
            != previous_handoff.source.plan_graph_identity
        ):
            raise FleetPlanInvalid(
                "Agent repair Plan/Step source changed; new authorization required"
            )
        matches = graph.agent_step_sources()
        if len(matches) != 1:
            raise FleetPlanInvalid("Agent repair requires exactly one original Agent Step")
        owner, step_id = matches[0]
        authored = next(
            step
            for step in graph.node(owner).compiled.steps
            if isinstance(step, AgentPlanStep) and step.step_id == step_id
        )
        require_agent_step_handoff(
            authored,
            previous_handoff,
            owner_source_sha256=graph.node(owner).source_sha256,
        )
        source = self._capture_agent_source_graph(
            graph,
            inputs=inputs,
            repository_root=repository_root,
            skill_root=skill_root,
            allowed_acceptance=allowed_acceptance,
            expected_source_sha256=previous_handoff.source.plan_source_sha256,
            skill_aliases=None,
            previous_source=previous_handoff.source,
            cancel_requested=cancel_requested,
            locked_skills=locked_skills,
        )
        handoff = replace(previous_handoff, source=source)
        handoff.verify()
        if cancel_requested is not None and cancel_requested():
            raise OperationCancelledError("Agent repair capture cancelled")
        return handoff

    def input_definitions(
        self,
        plan_path: Path,
        *,
        containment_root: Path | None = None,
        root_expected_name: str | None = None,
    ) -> tuple[PlanInputDefinition, ...]:
        inputs, _ = self.input_definitions_with_source(
            plan_path,
            containment_root=containment_root,
            root_expected_name=root_expected_name,
        )
        return inputs

    def input_definitions_with_source(
        self,
        plan_path: Path,
        *,
        containment_root: Path | None = None,
        root_expected_name: str | None = None,
    ) -> tuple[tuple[PlanInputDefinition, ...], str]:
        """Project inputs from one source cut and return the exact root SHA-256."""
        source_graph = self.analyze_source(
            plan_path,
            containment_root=containment_root,
            root_expected_name=root_expected_name,
        )
        source_sha256 = source_graph.root.source_sha256
        if source_sha256 is None:  # pragma: no cover - public path never uses overrides
            raise FleetPlanInvalid("Fleet plan source fingerprint is unavailable")
        return (
            materialize_projected_inputs(
                source_graph,
                source_graph.root.path,
            ).inputs,
            source_sha256,
        )

    def inspect_inputs_with_source(
        self,
        plan_path: Path,
        *,
        containment_root: Path | None = None,
        root_expected_name: str | None = None,
    ) -> tuple[tuple["InputInspection", ...], str]:
        """Preserve the definition-only view of the canonical input interface."""
        inspection, digest = self.inspect_input_interface_with_source(
            plan_path,
            containment_root=containment_root,
            root_expected_name=root_expected_name,
        )
        return inspection.inputs, digest

    def inspect_input_interface_with_source(
        self,
        plan_path: Path,
        *,
        containment_root: Path | None = None,
        root_expected_name: str | None = None,
    ) -> tuple[InputInterfaceInspection, str]:
        """Inspect one canonical source cut and report only the *root-file* digest.

        Never label this root SHA-256 as a full imported-Goon closure identity.
        """
        graph = self.analyze_source(
            plan_path,
            containment_root=containment_root,
            root_expected_name=root_expected_name,
        )
        digest = graph.root.source_sha256
        if digest is None:
            raise FleetPlanInvalid("Fleet plan source fingerprint is unavailable")
        return inspect_input_interface(graph), digest

    def inspect_inputs(
        self,
        plan_path: Path,
        *,
        containment_root: Path | None = None,
        root_expected_name: str | None = None,
    ) -> tuple["InputInspection", ...]:
        """Expose canonical input contracts and ultimate Goon owners, without effects."""
        inspections, _ = self.inspect_inputs_with_source(
            plan_path,
            containment_root=containment_root,
            root_expected_name=root_expected_name,
        )
        return inspections

    def resolve(
        self,
        plan_path: Path,
        *,
        extensions_path: Path | None = None,
        inputs: dict[str, PlanInputValue] | None = None,
        containment_root: Path | None = None,
        compiled_overrides: Mapping[Path, CompiledExecutionTemplate] | None = None,
        root_expected_name: str | None = None,
        repository: Path | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> ResolvedFleetPlan:
        source_graph = self.analyze_source(
            plan_path,
            containment_root=containment_root,
            compiled_overrides=compiled_overrides,
            root_expected_name=root_expected_name,
        )
        return self.resolve_source_graph(
            source_graph,
            extensions_path=extensions_path,
            inputs=inputs,
            repository=repository,
            environment=environment,
        )

    def preview(
        self,
        plan_path: Path,
        repository: Path,
        *,
        extensions_path: Path | None = None,
        inputs: dict[str, PlanInputValue] | None = None,
    ) -> FleetPlanPreview:
        """Observe repository-specific Plan readiness using the original preview owner.

        This read-only authoring operation neither admits nor prepares a Run.
        Its repository checks remain authoritative in ``preview_plan``.
        """
        plan = self.resolve(plan_path, extensions_path=extensions_path, inputs=inputs)
        return preview_plan(repository, plan)

    def resolve_with_source(
        self,
        plan_path: Path,
        *,
        extensions_path: Path | None = None,
        inputs: dict[str, PlanInputValue] | None = None,
        containment_root: Path | None = None,
        root_expected_name: str | None = None,
        repository: Path | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> tuple[ResolvedFleetPlan, str]:
        """Resolve from one root-file byte snapshot and return its exact SHA-256."""
        source_graph = self.analyze_source(
            plan_path,
            containment_root=containment_root,
            root_expected_name=root_expected_name,
        )
        plan, _, source_sha256 = self._resolve_graph(
            source_graph,
            extensions_path=extensions_path,
            inputs=inputs,
            trace=None,
            repository=repository,
            environment=environment,
        )
        if source_sha256 is None:  # pragma: no cover - public path never uses overrides
            raise FleetPlanInvalid("Fleet plan source fingerprint is unavailable")
        return plan, source_sha256

    def check(
        self,
        root: Path,
        *,
        extensions_path: Path | None = None,
        inputs: dict[str, PlanInputValue] | None = None,
    ) -> FleetPlanCheckReport:
        """Existing syntax checker plus one canonical resolution, without Fleet state."""
        from oh_goon.core.errors import FleetDomainError
        from oh_goon.integrations.fleet_plan_diagnostics import check_plan_sources

        report = check_plan_sources(root)
        if report.issues or not navigation_path(root).is_file():
            return report
        try:
            self.resolve(root, extensions_path=extensions_path, inputs=inputs)
        except (FleetDomainError, ValueError) as exc:
            return report.model_copy(
                update={
                    "issues": (*report.issues, PlanCheckIssue(path=root.name, message=str(exc)))
                }
            )
        return report

    def diff(
        self,
        before_path: Path,
        after_path: Path,
        *,
        extensions_path: Path | None = None,
        inputs: dict[str, PlanInputValue] | None = None,
    ) -> FleetPlanSemanticDiff:
        """Original resolver and original semantic projection, without runtime state."""
        from oh_goon.integrations.fleet_plan_diagnostics import semantic_plan_diff

        before = self.resolve(before_path, extensions_path=extensions_path, inputs=inputs)
        after = self.resolve(after_path, extensions_path=extensions_path, inputs=inputs)
        return semantic_plan_diff(before, after)

    def explain(
        self,
        plan_path: Path,
        *,
        extensions_path: Path | None = None,
        inputs: dict[str, PlanInputValue] | None = None,
        containment_root: Path | None = None,
        compiled_overrides: Mapping[Path, CompiledExecutionTemplate] | None = None,
        repository: Path | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> FleetPlanResolutionReport:
        _, report = self.resolve_and_explain(
            plan_path,
            extensions_path=extensions_path,
            inputs=inputs,
            containment_root=containment_root,
            compiled_overrides=compiled_overrides,
            repository=repository,
            environment=environment,
        )
        return report

    def resolve_and_explain(
        self,
        plan_path: Path,
        *,
        extensions_path: Path | None = None,
        inputs: dict[str, PlanInputValue] | None = None,
        containment_root: Path | None = None,
        compiled_overrides: Mapping[Path, CompiledExecutionTemplate] | None = None,
        repository: Path | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> tuple[ResolvedFleetPlan, FleetPlanResolutionReport]:
        trace: list[PlanResolutionEntry] = []
        value_trace: list[PlanValueResolutionEntry] = []
        source_graph = self.analyze_source(
            plan_path, containment_root=containment_root, compiled_overrides=compiled_overrides
        )
        plan, root, _ = self._resolve_graph(
            source_graph,
            extensions_path=extensions_path,
            inputs=inputs,
            trace=trace,
            value_trace=value_trace,
            repository=repository,
            environment=environment,
        )
        report = FleetPlanResolutionReport(
            plan_id=plan.plan_id,
            name=plan.name,
            root=str(root),
            configuration=plan.configuration,
            values=tuple(value_trace),
            entries=tuple(trace),
        )
        return plan, report

    def explain_routing(
        self,
        plan_path: Path,
        *,
        extensions_path: Path | None = None,
        inputs: dict[str, PlanInputValue] | None = None,
        caller_file: CallerFileRoutingOrigin | None = None,
        containment_root: Path | None = None,
        repository: Path | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> dict[str, object]:
        """Resolve exactly once, then project occurrence routes from that same cut."""
        from oh_goon.services.fleet_plan_input_routing import project_input_routing

        graph = self.analyze_source(plan_path, containment_root=containment_root)
        values: list[PlanValueResolutionEntry] = []
        plan, _, _ = self._resolve_graph(
            graph,
            extensions_path=extensions_path,
            inputs=inputs,
            trace=None,
            value_trace=values,
            repository=repository,
            environment=environment,
        )
        return project_input_routing(
            graph,
            plan,
            tuple(values),
            caller_file=caller_file,
        )

    def explain_agent_sources(
        self,
        plan_path: Path,
        *,
        inputs: dict[str, PlanInputValue],
        repository_root: Path,
        skill_root: Path,
        allowed_acceptance: tuple[str, ...],
        expected_source_sha256: str,
        skill_aliases: Mapping[str, str] | None = None,
        private_native_roots: tuple[Path, ...] = (),
    ) -> dict[str, object]:
        """Read-only AG-01 projection through the original Plan/Goon graph and binder.

        This never creates a Run or authorizes an Agent Step. Only explicitly
        supplied source roots may be read; the original compiler remains owner.
        """
        from oh_goon.services.agent_authoring_sources import explain_agent_sources
        from oh_goon.services.agent_skill_registry import original_skill_aliases

        source_graph = self.analyze_source(plan_path)
        if source_graph.root.source_sha256 != expected_source_sha256:
            raise FleetPlanInvalid("AG-01 source revision changed before inspection")
        compiled = materialize_projected_inputs(
            source_graph,
            source_graph.root.path,
        )
        values = bind_inputs(compiled, inputs, context=str(plan_path))
        with original_skill_aliases(skill_root, skill_aliases) as aliases:
            return explain_agent_sources(
                compiled.inputs,
                values,
                repository_root=repository_root,
                skill_root=skill_root,
                allowed_acceptance=allowed_acceptance,
                plan_source_sha256=expected_source_sha256,
                skill_aliases=aliases,
                private_native_roots=private_native_roots,
            )

    def capture_agent_sources(
        self,
        plan_path: Path,
        *,
        inputs: dict[str, PlanInputValue],
        repository_root: Path,
        skill_root: Path,
        allowed_acceptance: tuple[str, ...],
        expected_source_sha256: str,
        skill_aliases: Mapping[str, str] | None = None,
        private_native_roots: tuple[Path, ...] = (),
    ) -> "FrozenAgentSourceCapture":
        """AG-02 prework capture using the same original graph and source reader.

        This is NOT Step admission or transport. Ordinary resolve() still
        rejects typed agent sources before effects until the original executor
        can consume the sealed bytes under admitted authority.
        """
        source_graph = self.analyze_source(plan_path)
        return self._capture_agent_source_graph(
            source_graph,
            inputs=inputs,
            repository_root=repository_root,
            skill_root=skill_root,
            allowed_acceptance=allowed_acceptance,
            expected_source_sha256=expected_source_sha256,
            skill_aliases=skill_aliases,
            private_native_roots=private_native_roots,
        )

    def _capture_agent_source_graph(
        self,
        source_graph: FleetPlanSourceGraph,
        *,
        inputs: dict[str, PlanInputValue],
        repository_root: Path,
        skill_root: Path,
        allowed_acceptance: tuple[str, ...],
        expected_source_sha256: str,
        skill_aliases: Mapping[str, str] | None,
        previous_source: "FrozenAgentSourceCapture | None" = None,
        cancel_requested: Callable[[], bool] | None = None,
        locked_skills: tuple[LockedSkillSource, ...] | None = None,
        private_native_roots: tuple[Path, ...] = (),
    ) -> "FrozenAgentSourceCapture":
        """One graph cut reused by AG-02A capture and AG-02B Step binding."""
        from oh_goon.services.agent_authoring_sources import capture_agent_sources
        from oh_goon.services.agent_skill_registry import original_skill_aliases

        if source_graph.root.source_sha256 != expected_source_sha256:
            raise FleetPlanInvalid("AG-02 source revision changed before capture")
        compiled = materialize_projected_inputs(
            source_graph,
            source_graph.root.path,
        )
        values = bind_inputs(compiled, inputs, context=str(source_graph.root.path))
        with original_skill_aliases(
            skill_root,
            skill_aliases,
            locked_sources=locked_skills,
            cancel_requested=cancel_requested,
        ) as aliases:
            return capture_agent_sources(
                compiled.inputs,
                values,
                repository_root=repository_root,
                skill_root=skill_root,
                allowed_acceptance=allowed_acceptance,
                plan_source_sha256=expected_source_sha256,
                plan_graph_identity=source_graph.immutable_source_closure_identity(),
                skill_aliases=aliases,
                previous_source=previous_source,
                cancel_requested=cancel_requested,
                private_native_roots=private_native_roots,
            )

    def prepare_agent_step_handoff(
        self,
        plan_path: Path,
        *,
        inputs: dict[str, PlanInputValue],
        repository_root: Path,
        skill_root: Path,
        allowed_acceptance: tuple[str, ...],
        expected_source_sha256: str,
        skill_aliases: Mapping[str, str] | None = None,
    ) -> "FrozenAgentStepHandoff":
        """Bind captured bytes to one original authored Step. NO Run admission/effects.

        The original graph and binder are evaluated once. This handoff is not
        authorized to enter Fleet execution until the original admission and
        physical/disclosure safety gates exist; resolve() continues to reject.
        """
        from oh_goon.core.models.agent_source import FrozenAgentStepHandoff
        from oh_goon.core.models.fleet_plan import AgentPlanStep

        source_graph = self.analyze_source(plan_path)
        agents = source_graph.agent_step_sources()
        if len(agents) != 1:
            raise FleetPlanInvalid("AG-02B handoff requires exactly one typed agent Step")
        owner, step_id = agents[0]
        step = next(
            step
            for step in source_graph.node(owner).compiled.steps
            if isinstance(step, AgentPlanStep) and step.step_id == step_id
        )
        captured = self._capture_agent_source_graph(
            source_graph,
            inputs=inputs,
            repository_root=repository_root,
            skill_root=skill_root,
            allowed_acceptance=allowed_acceptance,
            expected_source_sha256=expected_source_sha256,
            skill_aliases=skill_aliases,
        )
        handoff = FrozenAgentStepHandoff(
            source=captured,
            owner_source_sha256=source_graph.node(owner).source_sha256 or "",
            step_id=step.step_id,
            harness=step.harness,
            task=step.task,
            timeout_seconds=step.timeout_seconds,
            requested_effects=step.requested_effects,
        )
        handoff.verify()
        return handoff

    def _resolve_graph(
        self,
        source_graph: FleetPlanSourceGraph,
        *,
        extensions_path: Path | None,
        inputs: dict[str, PlanInputValue] | None,
        trace: list[PlanResolutionEntry] | None,
        value_trace: list[PlanValueResolutionEntry] | None = None,
        repository: Path | None = None,
        environment: Mapping[str, str] | None = None,
        agent_handoff: FrozenAgentStepHandoff | None = None,
        agent_owner_path: Path | None = None,
        agent_capture: AgentCapture | None = None,
        agent_connection: AgentConnectionSelection | None = None,
        skill_sources: tuple[LockedSkillSource, ...]
        | Callable[[], tuple[LockedSkillSource, ...]] = (),
        native_agent: ResolvedNativeAgent | None = None,
        input_git_service: GitService | None = None,
        resolved_inputs: dict[str, PlanInputValue] | None = None,
        observe_environment: bool = True,
    ) -> tuple[ResolvedFleetPlan, Path, str | None]:
        plan_path = source_graph.root.path
        plan_root = source_graph.containment_root
        if repository is None and plan_path.name == "goon.yml":
            # Repository-owned CI/CD Goons are their own source boundary. This keeps
            # `goon lock` and direct inspection repository-aware without teaching
            # shared Plans to guess a target repository.
            repository = plan_path.parent
        compiled = materialize_projected_inputs(
            source_graph,
            source_graph.root.path,
        )
        source_sha256 = source_graph.root.source_sha256
        if compiled.execution is None:
            raise FleetPlanInvalid("Resolved execution policy is unavailable")
        catalog = load_extension_catalog(extensions_path) if extensions_path is not None else None
        extension_root = navigation_path(extensions_path).parent if extensions_path else None
        context = FleetPlanResolutionContext(
            plan_root=plan_root,
            execution=compiled.execution,
            catalog=catalog,
            extension_root=extension_root,
            extensions=self._extensions,
            tools=self._tools,
            diagnostics=FleetPlanResolutionDiagnostics(
                plan_root=plan_root, entries=trace, values=value_trace, source_graph=source_graph
            ),
            source_graph=source_graph,
            agent_handoff=agent_handoff,
            agent_owner_path=agent_owner_path,
            agent_capture=agent_capture,
            agent_connection=agent_connection,
            native_agent=native_agent,
            input_resolution=InputResolutionContext(
                repository=repository,
                environment=(os.environ if environment is None else environment),
                git=input_git_service or self._git_service,
                observe_environment=observe_environment,
            ),
        )
        # AG-01 is only a prework source inspection contract. Enabling typed
        # Agent Step effects belongs exclusively to AG-02's original admission.
        if source_graph.has_agent_source_contract() or source_graph.agent_step_sources():
            if agent_capture is None and (agent_handoff is None or agent_owner_path is None):
                raise FleetPlanInvalid(
                    "AG-02B typed agent sources/Steps are preview-only: original "
                    "Run/Step admission, LACE and disclosure gates are not qualified"
                )
        input_values = bind_inputs(compiled, inputs or {}, context=str(plan_path))
        if resolved_inputs is not None:
            resolved_inputs.update(input_values)
        if agent_handoff is not None:
            from oh_goon.services.agent_authoring_sources import agent_source_references

            captured = agent_handoff.source
            if (
                captured.plan_source_sha256 != source_sha256
                or captured.plan_graph_identity != source_graph.immutable_source_closure_identity()
                or agent_source_references(compiled.inputs, input_values)
                != (
                    captured.repository_reference,
                    captured.authored_skill_reference,
                    captured.acceptance_profile,
                )
            ):
                raise FleetPlanInvalid("Agent handoff source selection differs from resolved Plan")
        scope = resolve_compiled_steps(
            context,
            plan_path,
            compiled,
            prefix="",
            stack=(plan_path,),
            input_values=input_values,
            input_origins={
                name: ("supplied" if name in (inputs or {}) else "default") for name in input_values
            },
        )
        steps = _validate_backend_uv_cache_authority(scope.steps)
        validate_condition_dependencies(steps)
        try:
            plan = ResolvedFleetPlan.create(
                name=compiled.name,
                description=compiled.description,
                execution=compiled.execution,
                requirements=scope.requirements,
                imports=scope.imports,
                steps=steps,
                step_sources=scope.step_sources,
                skill_sources=skill_sources() if callable(skill_sources) else skill_sources,
                configuration=context.diagnostics.evidence(),
                native_agent=native_agent,
            )
        except ValidationError as exc:
            raise FleetPlanInvalid(f"Invalid resolved Fleet Plan: {exc}") from exc
        return plan, plan_root, source_sha256


def _validate_backend_uv_cache_authority(
    steps: tuple[ResolvedPlanStep, ...],
) -> tuple[ResolvedPlanStep, ...]:
    """Keep repository-backed uv cache authority single-owned by run preparation.

    Strict preparation prepares each exact project/group once through native ``uv sync --locked``
    and freezes that retained run-owned uv cache before application Step work. Physical
    attempts receive a copy of that frozen cache and materialize their own environment
    offline. Resolver-authored Step cache publication/consumption would create a second
    cache owner and, worse, would be overwritten by the prepared native cache when the
    next attempt is created. Authors therefore cannot declare cache lineage for managed
    uv Steps, and the resolver must not synthesize any either.
    """

    empty_cache = PlanStepCache()
    for step in steps:
        if not isinstance(step, ResolvedCommandStep) or step.uv is None:
            continue
        if step.cache != empty_cache:
            raise FleetPlanInvalid(
                f"Fleet UV step {step.step_id} cannot author cache lineage; it is backend-owned"
            )
    return steps


def _containment_root(plan_path: Path, containment_root: Path | None) -> Path:
    if containment_root is None:
        return plan_path.parent
    root = navigation_path(containment_root)
    if not root.is_dir():
        raise FleetPlanInvalid(f"Fleet plan containment root is not a directory: {root}")
    RepositoryPathSpace.from_root(root).relative(plan_path, label="Fleet plan path")
    return root
