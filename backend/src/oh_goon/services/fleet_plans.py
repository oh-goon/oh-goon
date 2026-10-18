from __future__ import annotations

from pathlib import Path

from oh_goon.core.errors import FleetDomainError, FleetPlanInvalid
from oh_goon.core.execution_lock_identity import build_execution_lock, verify_execution_lock
from oh_goon.core.models.caller_input_file import CallerInputFileIdentity, ScheduledCallerFileReceipt
from oh_goon.services.fleet_caller_file_freshness import require_caller_file_fresh
from oh_goon.core.models.execution_identity import ExecutionIdentity
from oh_goon.core.models.fleet_plan import ManagedExtension, ResolvedFleetPlan, ResolvedLocalSearchAgentStep
from oh_goon.core.models.fleet_plan_composition import PlanInputDefinition, PlanInputValue
from oh_goon.core.models.fleet_plan_diagnostics import (
    FleetPlanCheckReport,
    FleetPlanResolutionReport,
    FleetPlanSemanticDiff,
)
from oh_goon.core.models.fleet_plan_evidence import (
    FleetPlanPreview,
)
from oh_goon.core.models.goon_plan_source import (
    FleetPlanSource,
    PlanFileSource,
    SharedPlanSource,
    TeamGoonSource,
)
from oh_goon.core.models.run import FleetRunState
from oh_goon.core.models.schedule import (
    ScheduleCreate,
    ScheduleDefinition,
    ScheduleReplace,
)
from oh_goon.core.models.scheduling import SchedulingPriority
from oh_goon.core.models.target import FleetPlanTarget, AgentStepDeliveryReference
from oh_goon.core.vocabulary import FleetRunSource, OperationAction
from oh_goon.engine.fleet_extensions import FleetExtensionStore
from oh_goon.engine.fleet_tools import FleetToolStore
from oh_goon.integrations.fleet_plan_preview import preview_plan
from oh_goon.services.execution_lock_persistence import (
    LOCK_FILE_NAME,
    load_execution_lock,
    write_execution_lock,
)
from oh_goon.services.fleet_plan_preparation import prepare_declared_plan_runtimes
from oh_goon.services.fleet_plan_input_routing import CallerFileRoutingOrigin
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
from oh_goon.services.fleet_run_control_plane import FleetRunControlPlane
from oh_goon.services.schedules import ScheduleService


class FleetPlanService:
    """Resolve declarative inputs once, then submit only typed plan intent to Fleet."""

    def __init__(
        self,
        resolver: FleetPlanResolver,
        runs: FleetRunControlPlane,
        schedules: ScheduleService,
        extensions: FleetExtensionStore,
        tools: FleetToolStore,
        *,
        content_store=None,
        standard_local_fleet: bool = False,
    ) -> None:
        self._resolver = resolver
        self._runs = runs
        self._schedules = schedules
        self._extensions = extensions
        self._tools = tools
        self._content_store = content_store
        self._standard_local_fleet = standard_local_fleet

    def require_cli_plan_preparation(self, fleet_run_id: str | None) -> None:
        """CLI ingress preflight without manufacturing a Plan or execution authority."""
        self.require_plan_ingress_preparation(fleet_run_id)

    def require_plan_ingress_preparation(self, fleet_run_id: str | None) -> None:
        """Pre-resolution Run enrollment for CLI/HTTP; final admission is separate."""
        self._runs.require_plan_ingress_preparation(fleet_run_id)

    def resolve(
        self,
        plan_path: Path,
        *,
        extensions_path: Path | None = None,
        inputs: dict[str, PlanInputValue] | None = None,
        root_expected_name: str | None = None,
    ) -> ResolvedFleetPlan:
        return self._resolver.resolve(
            plan_path, extensions_path=extensions_path, inputs=inputs, root_expected_name=root_expected_name
        )

    def resolve_with_source(
        self,
        plan_path: Path,
        *,
        extensions_path: Path | None = None,
        inputs: dict[str, PlanInputValue] | None = None,
        root_expected_name: str | None = None,
    ) -> tuple[ResolvedFleetPlan, str]:
        return self._resolver.resolve_with_source(
            plan_path,
            extensions_path=extensions_path,
            inputs=inputs,
            root_expected_name=root_expected_name,
        )

    def input_definitions(
        self,
        plan_path: Path,
        *,
        root_expected_name: str | None = None,
    ) -> tuple[PlanInputDefinition, ...]:
        return self._resolver.input_definitions(
            plan_path, root_expected_name=root_expected_name
        )

    def input_definitions_with_source(
        self,
        plan_path: Path,
        *,
        root_expected_name: str | None = None,
    ) -> tuple[tuple[PlanInputDefinition, ...], str]:
        return self._resolver.input_definitions_with_source(
            plan_path, root_expected_name=root_expected_name
        )

    def inspect_inputs_with_source(
        self,
        plan_path: Path,
        *,
        root_expected_name: str | None = None,
    ):
        """Thin read-only adapter for the canonical resolver's single source cut."""
        return self._resolver.inspect_inputs_with_source(
            plan_path, root_expected_name=root_expected_name
        )

    def lock(
        self,
        plan_path: Path,
        *,
        extensions_path: Path | None = None,
        inputs: dict[str, PlanInputValue] | None = None,
        output: Path | None = None,
    ):
        return _generate_execution_lock(
            self._resolver, plan_path, extensions_path=extensions_path, inputs=inputs, output=output
        )

    def resolve_locked(
        self,
        plan_path: Path,
        *,
        extensions_path: Path | None,
        inputs: dict[str, PlanInputValue] | None,
        lock_path: Path | None = None,
    ):
        return _resolve_locked_execution(
            self._resolver,
            plan_path,
            extensions_path=extensions_path,
            inputs=inputs,
            lock_path=lock_path,
        )

    def prepare_execution_target(
        self,
        plan_path: Path,
        *,
        extensions_path: Path | None = None,
        inputs: dict[str, PlanInputValue] | None = None,
        source: FleetPlanSource | None = None,
        expected_plan_id: str | None = None,
        root_expected_name: str | None = None,
        lock_path: Path | None = None,
        caller_input_file: CallerInputFileIdentity | None = None,
    ) -> FleetPlanTarget:
        """Authoritative execution-entry boundary: resolve once, verify, then freeze target."""
        return prepare_execution_target(
            self._resolver,
            plan_path,
            extensions_path=extensions_path,
            inputs=inputs,
            source=source,
            expected_plan_id=expected_plan_id,
            root_expected_name=root_expected_name,
            lock_path=lock_path,
            caller_input_file=caller_input_file,
        )

    def prepare_reviewed_execution_target(
        self,
        plan_path: Path,
        *,
        expected_execution_digest: str,
        extensions_path: Path | None = None,
        inputs: dict[str, PlanInputValue] | None = None,
        source: FleetPlanSource | None = None,
        expected_plan_id: str | None = None,
        root_expected_name: str | None = None,
    ) -> FleetPlanTarget:
        return prepare_reviewed_execution_target(
            self._resolver, plan_path, expected_execution_digest=expected_execution_digest,
            extensions_path=extensions_path, inputs=inputs, source=source,
            expected_plan_id=expected_plan_id, root_expected_name=root_expected_name,
        )

    def explain(
        self,
        plan_path: Path,
        *,
        extensions_path: Path | None = None,
        inputs: dict[str, PlanInputValue] | None = None,
    ) -> FleetPlanResolutionReport:
        return self._resolver.explain(plan_path, extensions_path=extensions_path, inputs=inputs)

    def explain_routing(
        self,
        plan_path: Path,
        *,
        extensions_path: Path | None = None,
        inputs: dict[str, PlanInputValue] | None = None,
        caller_file: CallerFileRoutingOrigin | None = None,
    ) -> dict[str, object]:
        """Thin adapter; the canonical resolver owns all values and source cuts."""
        return self._resolver.explain_routing(
            plan_path, extensions_path=extensions_path, inputs=inputs,
            caller_file=caller_file,
        )

    def check(
        self,
        root: Path,
        *,
        extensions_path: Path | None = None,
        inputs: dict[str, PlanInputValue] | None = None,
    ) -> FleetPlanCheckReport:
        return self._resolver.check(root, extensions_path=extensions_path, inputs=inputs)

    def diff(
        self,
        before_path: Path,
        after_path: Path,
        *,
        extensions_path: Path | None = None,
        inputs: dict[str, PlanInputValue] | None = None,
    ) -> FleetPlanSemanticDiff:
        return self._resolver.diff(
            before_path, after_path, extensions_path=extensions_path, inputs=inputs
        )

    def prepare(
        self,
        plan_path: Path,
        *,
        extensions_path: Path | None = None,
        inputs: dict[str, PlanInputValue] | None = None,
    ) -> tuple[ResolvedFleetPlan, tuple[ManagedExtension, ...]]:
        """Acquire declared runtimes, then return the exact runnable resolved plan."""
        source_graph = self._resolver.analyze_source(plan_path)
        imported = prepare_declared_plan_runtimes(
            source_graph,
            extensions_path=extensions_path,
            extensions=self._extensions,
            tools=self._tools,
        )
        return (
            self._resolver.resolve_source_graph(
                source_graph, extensions_path=extensions_path, inputs=inputs
            ),
            imported,
        )

    def preview(
        self,
        plan_path: Path,
        repository: Path,
        *,
        extensions_path: Path | None = None,
        inputs: dict[str, PlanInputValue] | None = None,
    ) -> FleetPlanPreview:
        return self._resolver.preview(
            plan_path, repository, extensions_path=extensions_path, inputs=inputs
        )

    def preview_resolved(self, plan: ResolvedFleetPlan, repository: Path) -> FleetPlanPreview:
        return preview_plan(repository, plan)

    def run(
        self,
        plan_path: Path,
        *,
        extensions_path: Path | None = None,
        repository_ids: set[str] | None = None,
        all_repositories: bool = False,
        fleet_run_id: str | None = None,
        requested_priority: SchedulingPriority = SchedulingPriority.NORMAL,
        inputs: dict[str, PlanInputValue] | None = None,
        source: FleetPlanSource | None = None,
        expected_plan_id: str | None = None,
        expected_repository_instance_ids: dict[str, str] | None = None,
        root_expected_name: str | None = None,
        lock_path: Path | None = None,
        caller_input_file: CallerInputFileIdentity | None = None,
        agent_repository_root: Path | None = None,
        agent_skill_root: Path | None = None,
        allowed_agent_acceptance: tuple[str, ...] = (),
    ) -> FleetRunState:
        # RUN-7: Python/service ingress must not bypass the same original
        # enrolled-Run pre-resolution cut as CLI/HTTP. This checks membership
        # only; the Fleet lifecycle still owns final admission and revocation.
        self.require_plan_ingress_preparation(fleet_run_id)
        agent_selected = bool(agent_repository_root or agent_skill_root or allowed_agent_acceptance)
        if agent_selected:
            if (not self._standard_local_fleet or self._content_store is None
                    or agent_repository_root is None or agent_skill_root is None
                    or not allowed_agent_acceptance or caller_input_file is not None
                    or all_repositories or repository_ids is None or len(repository_ids) != 1
                    or source is not None or root_expected_name is not None):
                raise FleetPlanInvalid(
                    "standard-local Agent requires explicit single-repository scope, "
                    "repository/Skill roots and acceptance profile"
                )
            plan, handoff, source_sha256 = self._resolver.resolve_frozen_local_search_agent(
                plan_path, inputs=inputs or {}, repository_root=agent_repository_root,
                skill_root=agent_skill_root, allowed_acceptance=allowed_agent_acceptance,
                extensions_path=extensions_path,
            )
            if expected_plan_id is not None and expected_plan_id != plan.plan_id:
                raise FleetPlanInvalid("Agent Plan identity changed after review")
            from oh_goon.core.product_identity import repository_id
            repo_path = agent_repository_root / handoff.source.repository_reference.removeprefix("local:")
            owner = next(iter(repository_ids))
            if repository_id(repo_path) != owner:
                raise FleetPlanInvalid("frozen Agent repository differs from selected Fleet repository")
            from oh_goon.services.execution_lock_persistence import load_execution_lock
            lock = load_execution_lock(lock_path or (plan_path.parent / LOCK_FILE_NAME))
            verify_execution_lock(build_execution_lock(plan), lock)
            packet = handoff.encode_prework_delivery()
            stored = self._content_store.put_frozen_bytes(packet, owner_repository_id=owner)
            agent_step = next(item for item in plan.steps if isinstance(item, ResolvedLocalSearchAgentStep))
            target = FleetPlanTarget(
                kind="fleet_plan", plan=plan, source=PlanFileSource(sha256=source_sha256),
                identity=ExecutionIdentity(digest=lock.digest),
                agent_deliveries=(AgentStepDeliveryReference(
                    step_id=agent_step.step_id, handoff_identity=agent_step.handoff_identity,
                    delivery_sha256=agent_step.delivery_sha256, reference_id=stored.reference_id,
                ),),
            )
        else:
            target = self.prepare_execution_target(
                plan_path, extensions_path=extensions_path, inputs=inputs,
                source=source, expected_plan_id=expected_plan_id,
                root_expected_name=root_expected_name, lock_path=lock_path,
                caller_input_file=caller_input_file,
            )
        invocation_source = target.source
        return self._runs.start_prepared_plan(
            target,
            repository_ids=repository_ids,
            all_repositories=all_repositories,
            fleet_run_id=fleet_run_id,
            requested_priority=requested_priority,
            expected_repository_instance_ids=expected_repository_instance_ids,
            source=(
                FleetRunSource.CICD
                if isinstance(invocation_source, TeamGoonSource)
                else FleetRunSource.SHARED_RUN
                if isinstance(invocation_source, SharedPlanSource)
                else FleetRunSource.MANUAL
            ),
        )

    def run_reviewed(
        self,
        plan_path: Path,
        *,
        expected_execution_digest: str,
        repository_ids: set[str] | None = None,
        fleet_run_id: str | None = None,
        requested_priority: SchedulingPriority = SchedulingPriority.NORMAL,
        inputs: dict[str, PlanInputValue] | None = None,
        source: FleetPlanSource | None = None,
        expected_plan_id: str | None = None,
        expected_repository_instance_ids: dict[str, str] | None = None,
        root_expected_name: str | None = None,
    ) -> FleetRunState:
        # Reviewed direct calls also cross the existing ingress before any
        # resolution/digest work. Do not grant authority from the review hash.
        self.require_plan_ingress_preparation(fleet_run_id)
        target = self.prepare_reviewed_execution_target(
            plan_path, expected_execution_digest=expected_execution_digest, inputs=inputs,
            source=source, expected_plan_id=expected_plan_id, root_expected_name=root_expected_name,
        )
        return self._runs.start_prepared_plan(
            target, repository_ids=repository_ids, all_repositories=False, fleet_run_id=fleet_run_id,
            requested_priority=requested_priority, expected_repository_instance_ids=expected_repository_instance_ids,
            source=FleetRunSource.SHARED_RUN if isinstance(target.source, SharedPlanSource) else FleetRunSource.MANUAL,
        )

    def repository_instance_ids_for_target(
        self, target: FleetPlanTarget, *, repository_ids: set[str]
    ) -> dict[str, str]:
        """Snapshot current repository instances for one prepared Fleet Plan target."""
        return self._runs.repository_instance_ids_for_prepared_plan(
            target, repository_ids=repository_ids
        )

    def _prepare_scheduled_target(
        self, plan_path: Path, *, extensions_path: Path | None,
        inputs: dict[str, PlanInputValue] | None,
        caller_input_file: CallerInputFileIdentity | None,
    ) -> FleetPlanTarget:
        """Freeze caller-selected values once; future occurrences own no caller file.

        The existing resolver validates values. The only observed file is the
        same CLI-selected source, proven fresh before/after target construction.
        """
        if caller_input_file is not None:
            require_caller_file_fresh(caller_input_file)
        target = self.prepare_execution_target(
            plan_path, extensions_path=extensions_path, inputs=inputs,
            caller_input_file=caller_input_file,
        )
        if caller_input_file is None:
            return target
        # A scheduled occurrence must not require the creator's filesystem.
        # Reconstruct/validate the immutable target without a live-file pointer.
        frozen = ScheduledCallerFileReceipt(
            relative_path=caller_input_file.relative_path,
            sha256=caller_input_file.sha256,
            execution_digest=target.identity.digest,
        )
        sealed = FleetPlanTarget.model_validate({
            **target.model_dump(exclude={"caller_input_file", "scheduled_caller_file"}),
            "scheduled_caller_file": frozen.model_dump(),
        })
        require_caller_file_fresh(caller_input_file)
        return sealed

    def schedule(
        self,
        plan_path: Path,
        *,
        schedule_id: str,
        extensions_path: Path | None = None,
        repository_ids: set[str],
        name: str | None = None,
        interval_minutes: int,
        inputs: dict[str, PlanInputValue] | None = None,
        caller_input_file: CallerInputFileIdentity | None = None,
    ) -> ScheduleDefinition:
        """Resolve once, then persist one Schedule target set for the exact Plan."""
        target = self._prepare_scheduled_target(
            plan_path, extensions_path=extensions_path, inputs=inputs,
            caller_input_file=caller_input_file,
        )
        repository_instance_ids = self.repository_instance_ids_for_target(
            target, repository_ids=repository_ids
        )
        return self._schedules.create_prepared(
            schedule_id,
            _schedule_request(
                target,
                repository_ids=repository_ids,
                repository_instance_ids=repository_instance_ids,
                name=name,
                interval_minutes=interval_minutes,
            ),
        )

    def reschedule(
        self,
        plan_path: Path,
        *,
        schedule_id: str,
        extensions_path: Path | None = None,
        repository_ids: set[str],
        name: str | None = None,
        interval_minutes: int,
        inputs: dict[str, PlanInputValue] | None = None,
        caller_input_file: CallerInputFileIdentity | None = None,
    ) -> ScheduleDefinition:
        """CAS an existing Schedule target set onto a newly resolved immutable Plan."""
        current = self._schedules.definition(schedule_id)
        target = self._prepare_scheduled_target(
            plan_path, extensions_path=extensions_path, inputs=inputs,
            caller_input_file=caller_input_file,
        )
        repository_instance_ids = self.repository_instance_ids_for_target(
            target, repository_ids=repository_ids
        )
        desired = _schedule_request(
            target,
            repository_ids=repository_ids,
            repository_instance_ids=repository_instance_ids,
            name=name,
            interval_minutes=interval_minutes,
        )
        if _definition_matches(current, desired):
            return current
        return self._schedules.replace_prepared(
            schedule_id,
            ScheduleReplace(
                generation_id=current.generation_id,
                record_version=current.record_version,
                **desired.model_dump(),
            ),
        )


def _generate_execution_lock(
    resolver: FleetPlanResolver,
    plan_path: Path,
    *,
    extensions_path: Path | None,
    inputs: dict[str, PlanInputValue] | None,
    output: Path | None,
):
    graph = resolver.analyze_source(plan_path)
    plan = resolver.resolve_source_graph(graph, extensions_path=extensions_path, inputs=inputs)
    lock = build_execution_lock(plan)
    destination = output or (plan_path.parent / LOCK_FILE_NAME)
    try:
        write_execution_lock(destination, lock)
    except OSError as exc:
        raise FleetPlanInvalid(f"Cannot write execution lock {destination}: {exc}") from exc
    return lock


def _resolve_locked_execution(
    resolver: FleetPlanResolver,
    plan_path: Path,
    *,
    extensions_path: Path | None,
    inputs: dict[str, PlanInputValue] | None,
    root_expected_name: str | None = None,
    lock_path: Path | None = None,
):
    graph = resolver.analyze_source(plan_path, root_expected_name=root_expected_name)
    plan = resolver.resolve_source_graph(graph, extensions_path=extensions_path, inputs=inputs)
    actual = load_execution_lock(lock_path or (plan_path.parent / LOCK_FILE_NAME))
    expected = build_execution_lock(plan)
    verify_execution_lock(expected, actual)
    if graph.root.source_sha256 is None:
        raise FleetPlanInvalid("Fleet plan source fingerprint is unavailable")
    return plan, graph.root.source_sha256, actual.digest


def prepare_reviewed_execution_target(
    resolver: FleetPlanResolver,
    plan_path: Path,
    *,
    expected_execution_digest: str,
    extensions_path: Path | None = None,
    inputs: dict[str, PlanInputValue] | None = None,
    source: FleetPlanSource | None = None,
    expected_plan_id: str | None = None,
    root_expected_name: str | None = None,
) -> FleetPlanTarget:
    graph = resolver.analyze_source(plan_path, root_expected_name=root_expected_name)
    plan = resolver.resolve_source_graph(graph, extensions_path=extensions_path, inputs=inputs)
    lock = build_execution_lock(plan)
    if lock.digest != expected_execution_digest:
        raise FleetPlanInvalid("Fleet plan execution identity changed after preview; preview the exact plan again before execution")
    if graph.root.source_sha256 is None:
        raise FleetPlanInvalid("Fleet plan source fingerprint is unavailable")
    source_sha256 = graph.root.source_sha256
    if source is not None and source.sha256 != source_sha256:
        raise FleetPlanInvalid("Fleet plan source identity does not match the exact bytes resolved for execution")
    _require_expected_plan_identity(plan, expected_plan_id)
    return FleetPlanTarget(kind="fleet_plan", plan=plan, source=source or PlanFileSource(sha256=source_sha256), identity=ExecutionIdentity(digest=lock.digest))


def prepare_execution_target(
    resolver: FleetPlanResolver,
    plan_path: Path,
    *,
    extensions_path: Path | None = None,
    inputs: dict[str, PlanInputValue] | None = None,
    source: FleetPlanSource | None = None,
    expected_plan_id: str | None = None,
    root_expected_name: str | None = None,
    lock_path: Path | None = None,
    caller_input_file: CallerInputFileIdentity | None = None,
) -> FleetPlanTarget:
    """Resolve and verify one locked source into the only admissible Plan target shape."""
    if caller_input_file is not None:
        require_caller_file_fresh(caller_input_file)
    plan, source_sha256, execution_digest = _resolve_locked_execution(
        resolver,
        plan_path,
        extensions_path=extensions_path,
        inputs=inputs,
        root_expected_name=root_expected_name,
        lock_path=lock_path,
    )
    if source is not None and source.sha256 != source_sha256:
        raise FleetPlanInvalid(
            "Fleet plan source identity does not match the exact bytes resolved for execution"
        )
    _require_expected_plan_identity(plan, expected_plan_id)
    if caller_input_file is not None:
        require_caller_file_fresh(caller_input_file)
    return FleetPlanTarget(
        kind="fleet_plan",
        plan=plan,
        source=source or PlanFileSource(sha256=source_sha256),
        identity=ExecutionIdentity(digest=execution_digest),
        caller_input_file=caller_input_file,
    )


def _require_expected_plan_identity(plan: ResolvedFleetPlan, expected_plan_id: str | None) -> None:
    if expected_plan_id is not None and plan.plan_id != expected_plan_id:
        raise FleetPlanInvalid(
            "Fleet plan changed after preview; preview the exact plan again before execution"
        )


def _resolve_plan_source(
    resolver: FleetPlanResolver,
    plan_path: Path,
    *,
    extensions_path: Path | None,
    inputs: dict[str, PlanInputValue] | None,
    source: FleetPlanSource | None = None,
) -> tuple[ResolvedFleetPlan, FleetPlanSource]:
    plan, source_sha256 = resolver.resolve_with_source(
        plan_path, extensions_path=extensions_path, inputs=inputs
    )
    if source is not None and source.sha256 != source_sha256:
        raise FleetPlanInvalid(
            "Fleet plan source identity does not match the exact bytes resolved for execution"
        )
    return plan, source or PlanFileSource(sha256=source_sha256)


def _schedule_request(
    target: FleetPlanTarget,
    *,
    repository_ids: set[str],
    repository_instance_ids: dict[str, str],
    name: str | None,
    interval_minutes: int,
) -> ScheduleCreate:
    return ScheduleCreate(
        name=name or target.plan.name,
        repository_ids=tuple(sorted(repository_ids)),
        repository_instance_ids=repository_instance_ids,
        target=target,
        interval_minutes=interval_minutes,
    )


def _definition_matches(definition: ScheduleDefinition, desired: ScheduleCreate) -> bool:
    return (
        ScheduleCreate(
            name=definition.name,
            repository_ids=definition.repository_ids,
            repository_instance_ids=definition.repository_instance_ids,
            target=definition.target,
            interval_minutes=definition.interval_minutes,
            state=definition.state,
        )
        == desired
    )
