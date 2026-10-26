from __future__ import annotations

import json
import os
import shlex
import time
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager, nullcontext
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING

import yaml

from oh_goon.core.agent_review_plan import agent_phase_role
from oh_goon.core.diagnostic_redaction import redact_diagnostic_text
from oh_goon.core.errors import (
    FleetPlanInvalid,
    InvalidRunTransition,
    ScheduleNotFound,
    StateConflict,
)
from oh_goon.core.execution_lock_identity import build_execution_lock, verify_execution_lock
from oh_goon.core.models.agent_providers import (
    AgentConnection,
    AgentProvidersConfig,
    ResolvedNativeAgent,
)
from oh_goon.core.models.agent_repair import (
    MAX_AGENT_CAMPAIGN_MESSAGE_CHARS,
    TRUNCATED_AGENT_CAMPAIGN_MESSAGE,
    AgentCampaignDecision,
    AgentCampaignIntent,
    AgentCampaignUnstartedSelection,
    AgentRepairBudget,
    AgentRepairContext,
    AgentRepairResult,
)
from oh_goon.core.models.agent_repair import (
    AgentRepairStopReason as RepairStop,
)
from oh_goon.core.models.caller_input_file import (
    CallerInputFileIdentity,
    ScheduledCallerFileReceipt,
)
from oh_goon.core.models.execution_identity import ExecutionIdentity
from oh_goon.core.models.execution_lock_models import ExecutionLock, LockedExternalArtifact
from oh_goon.core.models.fleet_plan import (
    MAX_AGENT_TASK_CHARS,
    AgentPlanStep,
    ManagedExtension,
    ResolvedAgentStep,
    ResolvedFleetPlan,
    ResolvedPlanStep,
)
from oh_goon.core.models.fleet_plan_composition import PlanInputDefinition, PlanInputValue
from oh_goon.core.models.fleet_plan_diagnostics import (
    FleetPlanCheckReport,
    FleetPlanResolutionReport,
    FleetPlanSemanticDiff,
    PlanCallerOrigin,
)
from oh_goon.core.models.fleet_plan_evidence import (
    FleetPlanPreview,
)
from oh_goon.core.models.fleet_plan_execution_policy import FleetPlanRunLimits
from oh_goon.core.models.goon_plan_source import (
    FleetPlanSource,
    PlanFileSource,
    SharedPlanSource,
    TeamGoonSource,
)
from oh_goon.core.models.run import FleetRunDetail, FleetRunState
from oh_goon.core.models.run_configuration import RunAgentSelection
from oh_goon.core.models.schedule import (
    ScheduleCreate,
    ScheduleDefinition,
    ScheduleReplace,
)
from oh_goon.core.models.scheduling import SchedulingPriority
from oh_goon.core.models.target import AgentStepDeliveryReference, FleetPlanTarget
from oh_goon.core.path_authority import navigation_path
from oh_goon.core.product_identity import new_fleet_run_id
from oh_goon.core.run_identity import with_retained_project_deliveries
from oh_goon.core.time import utc_now
from oh_goon.core.vocabulary import FleetRunSource
from oh_goon.engine.command import CommandTerminal
from oh_goon.engine.errors import OperationCancelledError
from oh_goon.engine.fleet_extensions import FleetExtensionStore
from oh_goon.engine.fleet_tools import FleetToolStore
from oh_goon.engine.git import GitService
from oh_goon.integrations.fleet_plan_preview import preview_plan
from oh_goon.integrations.fleet_plan_process_isolation import (
    ProcessIsolationBackend,
    require_native_workspace_runtime_separation,
)
from oh_goon.services.execution_lock_persistence import (
    LOCK_FILE_NAME,
    load_execution_lock,
    write_execution_lock,
)
from oh_goon.services.fleet_caller_file_freshness import require_caller_file_fresh
from oh_goon.services.fleet_plan_input_inspection import InputInspection, InputInterfaceInspection
from oh_goon.services.fleet_plan_input_routing import CallerFileRoutingOrigin
from oh_goon.services.fleet_plan_preparation import prepare_declared_plan_runtimes
from oh_goon.services.fleet_plan_repair import (
    AgentReviewRepairCampaign,
    PassedRepairTest,
    repair_attempt_decision,
    validate_agent_repair_plan,
)
from oh_goon.services.fleet_plan_resolution_context import (
    AgentCaptureOptions,
    AgentConnectionSelection,
    PreparedAgentPlan,
)
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
from oh_goon.services.fleet_plan_source_graph import FleetPlanSourceGraph
from oh_goon.services.fleet_run_control_plane import FleetRunControlPlane
from oh_goon.services.schedules import ScheduleService


def _native_prior_context(value: str | None) -> str | None:
    """Explicit prior memory is untrusted data, never admission or permission."""
    if value is None:
        return None
    if type(value) is not str:
        raise FleetPlanInvalid("Prior Run handoff must be UTF-8 JSON text")
    try:
        if len(value.encode("utf-8")) > 65536:
            raise ValueError("oversize")
        prior = json.loads(value)
        if not isinstance(prior, dict):
            raise ValueError("not an object")
        context = json.dumps(
            {
                "authority": "UNTRUSTED_PRIOR_RUN_CONTEXT",
                "permission_authority": "NONE; fresh source capture and phase admission required",
                "prior_run_handoff": prior,
            },
            ensure_ascii=True,
            separators=(",", ":"),
        )
        if len(context.encode("utf-8")) > 65536:
            raise ValueError("oversize")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise FleetPlanInvalid(
            "Prior Run handoff must be a complete UTF-8 JSON object within 65536 bytes"
        ) from None
    return context


if TYPE_CHECKING:
    from oh_goon.core.models.agent_source import FrozenAgentStepHandoff
    from oh_goon.integrations.fleet_content_store import FleetContentStore


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
        content_store: FleetContentStore | None = None,
        standard_local_fleet: bool = False,
        terminal: CommandTerminal | None = None,
        standard_process_isolation: ProcessIsolationBackend | None = None,
        git_service: GitService | None = None,
    ) -> None:
        self._resolver = resolver
        self._runs = runs
        self._schedules = schedules
        self._extensions = extensions
        self._tools = tools
        self._content_store = content_store
        self._standard_local_fleet = standard_local_fleet
        self._terminal = terminal
        self._standard_process_isolation = standard_process_isolation
        self._git_service = git_service

    def require_cli_plan_preparation(self, fleet_run_id: str | None) -> None:
        """CLI ingress preflight without manufacturing a Plan or execution authority."""
        self.require_plan_ingress_preparation(fleet_run_id)

    def run_native_agent(
        self,
        *,
        selected: ResolvedNativeAgent,
        workspace: Path,
        skill: Path | None,
        task: str,
        verification: tuple[tuple[str, ...], ...] = (),
        role: str = "report",
        fleet_run_id: str | None = None,
        prior_context: str | None = None,
    ) -> FleetRunState:
        """Author one ordinary native-workload Plan and admit it through Fleet.

        The temporary YAML is ordinary authoring input, compiled/captured once.
        Its resolved target and exact frozen source remain with the existing
        content/Run owners; this convenience owns no process or workflow loop.
        """
        if not self._standard_local_fleet or self._content_store is None:
            raise FleetPlanInvalid("Native Agent hosting requires ordinary Fleet execution")
        if (
            type(task) is not str
            or not task.strip()
            or "\x00" in task
            or len(task) > MAX_AGENT_TASK_CHARS
        ):
            raise FleetPlanInvalid(
                f"Native task requires 1 to {MAX_AGENT_TASK_CHARS} authored characters without NUL"
            )
        try:
            task.encode("utf-8")
        except UnicodeError:
            raise FleetPlanInvalid("Native task must be valid UTF-8") from None
        context = _native_prior_context(prior_context)
        self.require_plan_ingress_preparation(fleet_run_id)
        workspace = workspace.resolve(strict=True)
        if role not in {"report", "review"}:
            raise FleetPlanInvalid("Adjudication/proposal requires the original bounded campaign")
        if selected.interactive and (self._terminal is None or role != "report"):
            raise FleetPlanInvalid("Interactive Agent requires its original terminal transport")
        if selected.profile.sandbox_owner == "execution_backend" and role == "report":
            raise FleetPlanInvalid(
                "Protected native coding requires the read-only proposal and separately admitted scoped apply workflow"
            )
        selected = self._prepare_native_agent(
            selected, workspace, read_only_workspace=role != "report" and not verification
        )
        from oh_goon.core.product_identity import repository_id

        with TemporaryDirectory(prefix="oh-goon-native-plan-") as temporary:
            root = Path(temporary)
            package = root / "catalog/agents/session"
            package.mkdir(parents=True)
            if skill is None:
                selected_skill = root / "skills/oh-goon/native"
                selected_skill.mkdir(parents=True)
                (selected_skill / "SKILL.md").write_text(
                    "# Native coding workload\n\nFollow the supplied task within the approved "
                    "workspace. Report incomplete work and failures accurately. Native verification "
                    "results remain separate from agent claims.\n",
                    encoding="utf-8",
                )
            else:
                selected_skill = skill.resolve(strict=True)
                if selected_skill.is_file():
                    if selected_skill.name != "SKILL.md":
                        raise FleetPlanInvalid("Select the canonical SKILL.md or its directory")
                    selected_skill = selected_skill.parent
            recipients = list(selected.disclosure_recipients)
            steps = [
                {
                    "id": "native-agent",
                    "uses": "agent",
                    "harness": "native",
                    "role": role,
                    "task": task,
                    "timeout_seconds": selected.profile.timeout_seconds,
                    "requested_effects": {
                        "repository_access": "write" if role == "report" else "read_only",
                        "recipients": recipients,
                    },
                },
                *(
                    {"id": f"verify-{index}", "run": shlex.join(argv)}
                    for index, argv in enumerate(verification, 1)
                ),
            ]
            payload = {
                "inputs": {
                    "repo": {"type": "repository_reference"},
                    "skill": {"type": "skill_reference"},
                    "acceptance": {"type": "acceptance_profile"},
                },
                "steps": steps,
            }
            (package / "goon.yml").write_text(yaml.safe_dump(payload), encoding="utf-8")
            (package / "README.md").write_text("# agents/session\n", encoding="utf-8")
            plan_path = root / "plan.yml"
            plan_path.write_text(
                yaml.safe_dump({"goons": [{"import": "agents/session", "from": "catalog"}]}),
                encoding="utf-8",
            )
            plan, deliveries, source_sha = self._resolver.resolve_frozen_agents(
                plan_path,
                inputs={
                    "repo": f"local:{workspace.parent.name}/{workspace.name}",
                    "skill": f"local:{selected_skill.parent.name}/{selected_skill.name}",
                    "acceptance": "native-coding",
                },
                repository_root=workspace.parent.parent,
                skill_root=selected_skill.parent.parent,
                allowed_acceptance=("native-coding",),
                native_agent=selected,
                repository=workspace,
                git_service=self._git_service,
            )
            if context is not None:
                plan = ResolvedFleetPlan.create(
                    name=plan.name,
                    description=plan.description,
                    execution=plan.execution,
                    steps=tuple(
                        step.model_copy(update={"review_context": context})
                        if isinstance(step, ResolvedAgentStep)
                        else step
                        for step in plan.steps
                    ),
                    requirements=plan.requirements,
                    imports=plan.imports,
                    step_sources=plan.step_sources,
                    skill_sources=plan.skill_sources,
                    configuration=plan.configuration,
                    native_agent=plan.native_agent,
                )
            write_execution_lock(root / LOCK_FILE_NAME, build_execution_lock(plan))
            target = self._agent_target(plan, deliveries, source_sha, repository_id(workspace))
            return self._runs.start_prepared_plan(
                target,
                repository_ids={repository_id(workspace)},
                all_repositories=False,
                fleet_run_id=fleet_run_id,
                source=FleetRunSource.MANUAL,
                created_agent_deliveries=target.agent_deliveries,
            )

    def _prepare_native_agent(
        self,
        selected: ResolvedNativeAgent,
        workspace: Path,
        *,
        read_only_workspace: bool = False,
    ) -> ResolvedNativeAgent:
        """Bind and preflight the original backend before any native Run admission."""
        from oh_goon.integrations.fleet_plan_native_steps import require_native_agent_compatibility

        protected = selected.profile.sandbox_owner == "execution_backend"
        read_only_workspace = read_only_workspace or protected
        require_native_agent_compatibility(
            selected,
            role="review" if read_only_workspace else "report",
            terminal_available=self._terminal is not None,
        )
        require_native_workspace_runtime_separation(
            workspace, read_only_workspace=read_only_workspace
        )
        if selected.profile.codex_service is not None:
            from oh_goon.integrations.agent_native_execution import (
                require_codex_authentication_home_separation,
                verify_native_agent_executable,
            )

            service = selected.profile.codex_service
            home = Path(service.home)
            if not home.is_absolute() or ".." in home.parts:
                raise FleetPlanInvalid("Codex service requires an explicit native state directory")
            try:
                home = home.resolve(strict=True)
                if not home.is_dir():
                    raise OSError("not a directory")
            except (OSError, RuntimeError):
                raise FleetPlanInvalid("Codex service state directory is unavailable") from None
            require_codex_authentication_home_separation(
                home,
                (
                    workspace,
                    Path(selected.definition.executable or ""),
                    *(Path(root) for root in selected.definition.runtime_roots),
                ),
            )
            selected = replace(
                selected,
                profile=selected.profile.model_copy(
                    update={"codex_service": service.model_copy(update={"home": str(home)})}
                ),
            )
            verify_native_agent_executable(selected)
        if selected.profile.codex_login is not None:
            home = Path(selected.profile.codex_login.home)
            if workspace.is_relative_to(home) or home.is_relative_to(workspace):
                raise FleetPlanInvalid("Codex authentication home must be outside the workspace")
        if (
            read_only_workspace
            and selected.profile.backend != "bubblewrap"
            and selected.profile.codex_service is None
        ):
            raise FleetPlanInvalid("Native read-only phases require the selected isolated backend")
        if selected.profile.backend == "bubblewrap":
            if (
                self._git_service is None
                or self._git_service.command_runner is None
                or self._git_service.observation_runner is None
            ):
                raise FleetPlanInvalid(
                    "Native isolation requires the runtime's scoped Git execution boundary"
                )
            from oh_goon.integrations.fleet_plan_bubblewrap_isolation import (
                BubblewrapProcessIsolation,
            )
            from oh_goon.integrations.fleet_plan_process_isolation import (
                StandardIsolatedProcessRequest,
                native_runtime_read_only_mounts,
            )
            from oh_goon.runtime.workspaces import RepositoryWorkspaceAuthority

            backend = self._standard_process_isolation
            if not isinstance(backend, BubblewrapProcessIsolation):
                raise FleetPlanInvalid("The selected ordinary isolation backend is unavailable")
            identity = backend.configuration_identity()
            if selected.backend_configuration_id not in (None, identity):
                raise FleetPlanInvalid(
                    "Native selection differs from the resolved isolation backend"
                )
            selected = replace(selected, backend_configuration_id=identity)
            with RepositoryWorkspaceAuthority.pin_repository_subject(workspace) as require_current:
                backend.require_standard_available(
                    StandardIsolatedProcessRequest(
                        workspace=workspace,
                        argv=(selected.definition.executable or "",),
                        cwd=workspace,
                        env={},
                        backend_configuration_id=selected.backend_configuration_id or "",
                        require_workspace_current=require_current,
                        read_only_mounts=native_runtime_read_only_mounts(workspace, selected),
                        resources=selected.profile.resources,
                        read_only_workspace=read_only_workspace,
                        protected_execution=protected,
                        private_configuration_directory=(
                            Path(selected.definition.configuration_directory)
                            if selected.definition.configuration_directory is not None
                            else None
                        ),
                    )
                )
        elif any(value is not None for value in selected.profile.resources.model_dump().values()):
            raise FleetPlanInvalid("Local Agent resource caps require a capable isolated profile")
        return selected

    def require_plan_ingress_preparation(self, fleet_run_id: str | None) -> None:
        """Pre-resolution Run enrollment for CLI/HTTP; final admission is separate."""
        self._runs.require_plan_ingress_preparation(fleet_run_id)

    def prepare_agent_plan(
        self,
        plan_path: Path,
        *,
        selection: RunAgentSelection,
        repository: Path,
        inputs: dict[str, PlanInputValue],
        root_expected_name: str,
        require_review_phases: bool | None = None,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> PreparedAgentPlan:
        """Capture an explicit HTTP selection with the same canonical Agent resolver as CLI."""
        from oh_goon.runtime.agent_providers import (
            agent_providers_path,
            load_agent_providers,
            resolve_native_agent,
        )

        if not self._standard_local_fleet or self._content_store is None:
            raise FleetPlanInvalid("Agent Plans require ordinary local Fleet execution")
        if cancel_requested is not None and cancel_requested():
            raise OperationCancelledError("Agent preparation cancelled")
        try:
            selected_inputs = selection.caller_inputs(inputs)
        except ValueError as exc:
            raise FleetPlanInvalid(str(exc)) from exc
        roots = []
        for value in (selection.repository_root, selection.skill_root):
            path = Path(value)
            try:
                if not path.is_absolute() or ".." in path.parts:
                    raise ValueError("absolute root required")
                resolved = path.resolve(strict=True)
                if not resolved.is_dir() or resolved != path:
                    raise ValueError("direct directory required")
            except (OSError, RuntimeError, ValueError):
                raise FleetPlanInvalid(
                    "Shared Agent repository/Skill roots require existing absolute directories without symlinks"
                ) from None
            roots.append(resolved)
        graph = self._resolver.analyze_source(plan_path, root_expected_name=root_expected_name)
        graph.validate_agent_selection(
            native_selected=selection.implementation is not None,
            model_selected=selection.model_profile is not None,
            require_review_phases=require_review_phases,
        )
        native = None
        connection = None
        if selection.implementation is not None:
            config_path = agent_providers_path(os.environ)
            config = (
                load_agent_providers(config_path)
                if config_path.exists()
                else AgentProvidersConfig()
            )
            assert selection.profile is not None
            native = resolve_native_agent(
                config,
                selection.implementation,
                selection.profile,
                workspace=repository,
                environment=os.environ,
            )
            read_only_workspace = all(
                step.requested_effects.repository_access == "read_only"
                for owner, identifier in graph.agent_step_sources()
                for step in graph.node(owner).compiled.steps
                if isinstance(step, AgentPlanStep) and step.step_id == identifier
            )
            native = self._prepare_native_agent(
                native, repository, read_only_workspace=read_only_workspace
            )
        elif selection.model_profile is not None:
            connection = AgentConnection.select(
                load_agent_providers(agent_providers_path(os.environ)), selection.model_profile
            )
        if cancel_requested is not None and cancel_requested():
            raise OperationCancelledError("Agent preparation cancelled")
        skill_lock_path = roots[1] / LOCK_FILE_NAME
        locked_skills = (
            load_execution_lock(skill_lock_path).skill_sources
            if os.path.lexists(skill_lock_path)
            else ()
        )
        options: AgentCaptureOptions = dict(
            inputs=selected_inputs,
            repository_root=roots[0],
            skill_root=roots[1],
            allowed_acceptance=(selection.acceptance,),
            extensions_path=None,
            cancel_requested=cancel_requested,
            connection=connection,
            locked_skills=locked_skills,
            native_agent=native,
            repository=repository,
            git_service=self._git_service,
            source_graph=graph,
            require_review_phases=require_review_phases,
            root_expected_name=root_expected_name,
        )
        plan, handoffs, source_sha256 = self._resolver.resolve_frozen_agents(plan_path, **options)
        captured = roots[0] / next(
            iter(handoffs.values())
        ).source.repository_reference.removeprefix("local:")
        if captured.resolve(strict=True) != repository.resolve(strict=True):
            raise FleetPlanInvalid("Captured Agent repository differs from the selected Team")
        # The selected Skill pins travel with this capture and every later campaign capture.
        options["locked_skills"] = plan.skill_sources
        return PreparedAgentPlan(plan, handoffs, source_sha256, repository, options)

    def run_prepared_agent(
        self,
        prepared: PreparedAgentPlan,
        *,
        source: SharedPlanSource,
        expected_plan_id: str,
        expected_execution_digest: str,
        repository_ids: set[str],
        expected_repository_instance_ids: dict[str, str],
        fleet_run_id: str | None,
        requested_priority: SchedulingPriority,
    ) -> FleetRunState:
        self.require_plan_ingress_preparation(fleet_run_id)
        self._require_reviewed_agent(
            prepared,
            source,
            expected_plan_id,
            expected_execution_digest,
            repository_ids,
            expected_repository_instance_ids,
        )
        from oh_goon.core.agent_review_plan import agent_review_phases

        if agent_review_phases(prepared.plan) or any(
            agent_phase_role(step) in ("adjudication", "proposal") for step in prepared.plan.steps
        ):
            raise FleetPlanInvalid(
                "Phase-classified Agent Plans require an explicitly authorized bounded repair"
            )
        target = self._agent_target(
            prepared.plan,
            prepared.handoffs,
            prepared.source_sha256,
            next(iter(repository_ids)),
            source=source,
        )
        return self._runs.start_prepared_plan(
            target,
            repository_ids=repository_ids,
            fleet_run_id=fleet_run_id,
            expected_repository_instance_ids=expected_repository_instance_ids,
            requested_priority=requested_priority,
            source=FleetRunSource.SHARED_RUN,
            created_agent_deliveries=target.agent_deliveries,
        )

    def _require_reviewed_agent(
        self,
        prepared: PreparedAgentPlan,
        source: SharedPlanSource,
        expected_plan_id: str,
        expected_execution_digest: str,
        repository_ids: set[str],
        expected_repository_instance_ids: dict[str, str],
    ) -> None:
        from oh_goon.core.product_identity import repository_id, repository_subject_instance_id

        if (
            repository_ids != {repository_id(prepared.repository)}
            or set(expected_repository_instance_ids) != repository_ids
            or expected_repository_instance_ids[repository_id(prepared.repository)]
            != repository_subject_instance_id(prepared.repository)
        ):
            raise FleetPlanInvalid("Agent Team instance changed after preview")
        if source.sha256 != prepared.source_sha256:
            raise FleetPlanInvalid("Shared Agent source changed after review")
        _require_expected_plan_identity(prepared.plan, expected_plan_id)
        if build_execution_lock(prepared.plan).digest != expected_execution_digest:
            raise FleetPlanInvalid("Agent execution identity changed after preview; preview again")

    def resolve(
        self,
        plan_path: Path,
        *,
        extensions_path: Path | None = None,
        inputs: dict[str, PlanInputValue] | None = None,
        root_expected_name: str | None = None,
    ) -> ResolvedFleetPlan:
        return self._resolver.resolve(
            plan_path,
            extensions_path=extensions_path,
            inputs=inputs,
            root_expected_name=root_expected_name,
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
        return self._resolver.input_definitions(plan_path, root_expected_name=root_expected_name)

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
    ) -> tuple[tuple[InputInspection, ...], str]:
        """Thin read-only adapter for the canonical resolver's single source cut."""
        return self._resolver.inspect_inputs_with_source(
            plan_path, root_expected_name=root_expected_name
        )

    def inspect_input_interface_with_source(
        self,
        plan_path: Path,
        *,
        root_expected_name: str | None = None,
    ) -> tuple[InputInterfaceInspection, str]:
        return self._resolver.inspect_input_interface_with_source(
            plan_path, root_expected_name=root_expected_name
        )

    def lock(
        self,
        plan_path: Path,
        *,
        extensions_path: Path | None = None,
        inputs: dict[str, PlanInputValue] | None = None,
        output: Path | None = None,
        external_artifacts: tuple[tuple[str, bytes], ...] = (),
        caller_input_file: CallerInputFileIdentity | None = None,
        source_graph: FleetPlanSourceGraph | None = None,
        repository: Path | None = None,
    ) -> ExecutionLock:
        return _generate_execution_lock(
            self._resolver,
            plan_path,
            extensions_path=extensions_path,
            inputs=inputs,
            output=output,
            external_artifacts=external_artifacts,
            caller_input_file=caller_input_file,
            source_graph=source_graph,
            repository=repository,
        )

    def resolve_locked(
        self,
        plan_path: Path,
        *,
        extensions_path: Path | None,
        inputs: dict[str, PlanInputValue] | None,
        lock_path: Path | None = None,
        external_artifacts: tuple[tuple[str, bytes], ...] = (),
        source_graph: FleetPlanSourceGraph | None = None,
        repository: Path | None = None,
    ) -> tuple[ResolvedFleetPlan, str, str]:
        plan, source_sha256, lock = _resolve_locked_execution(
            self._resolver,
            plan_path,
            extensions_path=extensions_path,
            inputs=inputs,
            lock_path=lock_path,
            external_artifacts=external_artifacts,
            source_graph=source_graph,
            repository=repository,
        )
        return plan, source_sha256, lock.digest

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
        caller_input_origins: Mapping[str, PlanCallerOrigin] | None = None,
        external_artifacts: tuple[tuple[str, bytes], ...] = (),
        repository_goon: bool = False,
        source_graph: FleetPlanSourceGraph | None = None,
        repository: Path | None = None,
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
            caller_input_origins=caller_input_origins,
            external_artifacts=external_artifacts,
            repository_goon=repository_goon,
            source_graph=source_graph,
            repository=repository,
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
            self._resolver,
            plan_path,
            expected_execution_digest=expected_execution_digest,
            extensions_path=extensions_path,
            inputs=inputs,
            source=source,
            expected_plan_id=expected_plan_id,
            root_expected_name=root_expected_name,
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
            plan_path,
            extensions_path=extensions_path,
            inputs=inputs,
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
        caller_input_origins: Mapping[str, PlanCallerOrigin] | None = None,
        agent_repository_root: Path | None = None,
        agent_skill_root: Path | None = None,
        allowed_agent_acceptance: tuple[str, ...] = (),
        agent_handoff: "FrozenAgentStepHandoff | None" = None,
        agent_connection: AgentConnectionSelection | None = None,
        native_agent: ResolvedNativeAgent | None = None,
        native_workspace: Path | None = None,
        external_artifacts: tuple[tuple[str, bytes], ...] = (),
        repository_goon: bool = False,
        source_graph: FleetPlanSourceGraph | None = None,
        repository: Path | None = None,
    ) -> FleetRunState:
        # RUN-7: Python/service ingress must not bypass the same original
        # enrolled-Run pre-resolution cut as CLI/HTTP. This checks membership
        # only; the Fleet lifecycle still owns final admission and revocation.
        self.require_plan_ingress_preparation(fleet_run_id)
        if repository is not None:
            from oh_goon.core.product_identity import repository_id as identify_context_repository

            if all_repositories or repository_ids != {identify_context_repository(repository)}:
                raise FleetPlanInvalid("Resolution repository differs from selected Run scope")
            if native_workspace is not None and repository.resolve() != native_workspace.resolve():
                raise FleetPlanInvalid(
                    "Native workspace differs from selected resolution repository"
                )
        if source_graph is not None:
            _selected_source_graph(self._resolver, plan_path, source_graph=source_graph)
        if repository_goon:
            from oh_goon.core.product_identity import repository_id as identify_source_repository

            if (
                plan_path.name != "goon.yml"
                or all_repositories
                or repository_ids != {identify_source_repository(plan_path.parent)}
            ):
                raise FleetPlanInvalid(
                    "Repository Goon source requires the exact selected repository root goon.yml"
                )
        if caller_input_file is not None:
            require_caller_file_fresh(caller_input_file)
        if native_workspace is not None and native_agent is None:
            raise FleetPlanInvalid("A native workspace requires its frozen native selection")
        agent_selected = bool(
            agent_repository_root
            or agent_skill_root
            or allowed_agent_acceptance
            or agent_handoff is not None
            or native_agent is not None
        )
        if agent_selected:
            if native_agent is not None:
                if (
                    native_workspace is None
                    or agent_connection is not None
                    or agent_handoff is not None
                ):
                    raise FleetPlanInvalid(
                        "Native Plan requires one explicit workspace and its frozen native selection"
                    )
                native_workspace = native_workspace.resolve(strict=True)
                source_graph = _selected_source_graph(
                    self._resolver, plan_path, source_graph=source_graph
                )
                source_graph.validate_agent_selection(
                    native_selected=True, require_review_phases=False
                )
                read_only_workspace = all(
                    step.requested_effects.repository_access == "read_only"
                    for owner, identifier in source_graph.agent_step_sources()
                    for step in source_graph.node(owner).compiled.steps
                    if isinstance(step, AgentPlanStep) and step.step_id == identifier
                )
                # These are fixed authored effects from the same captured graph
                # passed to resolution below, not a second source interpretation.
                native_agent = self._prepare_native_agent(
                    native_agent, native_workspace, read_only_workspace=read_only_workspace
                )
            if (
                not self._standard_local_fleet
                or self._content_store is None
                or agent_repository_root is None
                or agent_skill_root is None
                or not allowed_agent_acceptance
                or all_repositories
                or repository_ids is None
                or len(repository_ids) != 1
                or source is not None
                or root_expected_name is not None
            ):
                raise FleetPlanInvalid(
                    "standard-local Agent requires explicit single-repository scope, "
                    "repository/Skill roots and acceptance profile"
                )
            lock = load_execution_lock(lock_path or (plan_path.parent / LOCK_FILE_NAME))
            handoff: FrozenAgentStepHandoff | dict[str, FrozenAgentStepHandoff]
            if agent_handoff is None:
                plan, handoff, source_sha256 = self._resolver.resolve_frozen_agents(
                    plan_path,
                    inputs=inputs or {},
                    repository_root=agent_repository_root,
                    skill_root=agent_skill_root,
                    allowed_acceptance=allowed_agent_acceptance,
                    extensions_path=extensions_path,
                    connection=agent_connection,
                    locked_skills=lock.skill_sources,
                    require_review_phases=False,
                    source_graph=source_graph,
                    native_agent=native_agent,
                    repository=native_workspace if native_agent is not None else repository,
                    git_service=self._git_service if native_agent is not None else None,
                )
            else:
                plan, handoff, source_sha256 = self._resolver.resolve_frozen_agent(
                    plan_path,
                    inputs=inputs or {},
                    repository_root=agent_repository_root,
                    skill_root=agent_skill_root,
                    allowed_acceptance=allowed_agent_acceptance,
                    extensions_path=extensions_path,
                    handoff=agent_handoff,
                    locked_skills=lock.skill_sources,
                    source_graph=source_graph,
                )
            from oh_goon.core.agent_review_plan import agent_review_phases

            if agent_review_phases(plan):
                raise FleetPlanInvalid(
                    "phase-classified Agent Plans require explicit review-pass, write-scope and deadline budgets"
                )
            if any(agent_phase_role(step) in ("adjudication", "proposal") for step in plan.steps):
                raise FleetPlanInvalid(
                    "adjudication and repair Agents require a review-repair loop"
                )
            if expected_plan_id is not None and expected_plan_id != plan.plan_id:
                raise FleetPlanInvalid("Agent Plan identity changed after review")
            from oh_goon.core.product_identity import repository_id

            selected_handoff = (
                next(iter(handoff.values())) if isinstance(handoff, dict) else handoff
            )
            repo_path = (
                agent_repository_root
                / selected_handoff.source.repository_reference.removeprefix("local:")
            )
            if native_workspace is not None and repo_path.resolve(strict=True) != native_workspace:
                raise FleetPlanInvalid(
                    "Captured Agent repository differs from the explicit native workspace"
                )
            if native_agent is not None and native_workspace is not None:
                assert isinstance(handoff, dict)
                require_native_workspace_runtime_separation(
                    native_workspace,
                    read_only_workspace=all(
                        isinstance(step, ResolvedAgentStep)
                        and handoff[step.step_id].requested_effects.repository_access == "read_only"
                        for step in plan.steps
                    ),
                )
            owner = next(iter(repository_ids))
            if repository_id(repo_path) != owner:
                raise FleetPlanInvalid(
                    "frozen Agent repository differs from selected Fleet repository"
                )
            verify_execution_lock(
                build_execution_lock(plan, external_artifacts=external_artifacts), lock
            )
            target = self._agent_target(
                plan,
                handoff,
                source_sha256,
                owner,
                caller_input_file=caller_input_file,
                repository_goon=repository_goon,
                lock_artifacts=lock.external,
            )
        else:
            target = self.prepare_execution_target(
                plan_path,
                extensions_path=extensions_path,
                inputs=inputs,
                source=source,
                expected_plan_id=expected_plan_id,
                root_expected_name=root_expected_name,
                lock_path=lock_path,
                caller_input_file=caller_input_file,
                caller_input_origins=caller_input_origins,
                external_artifacts=external_artifacts,
                repository_goon=repository_goon,
                source_graph=source_graph,
                repository=repository,
            )
        if agent_selected and target.plan.configuration is not None and caller_input_origins:
            target = target.model_copy(
                update={
                    "plan": target.plan.model_copy(
                        update={
                            "configuration": target.plan.configuration.with_caller_origins(
                                caller_input_origins
                            )
                        }
                    )
                }
            )
        if caller_input_file is not None:
            require_caller_file_fresh(caller_input_file)
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
            created_agent_deliveries=target.agent_deliveries if agent_selected else (),
        )

    def _agent_target(
        self,
        plan: ResolvedFleetPlan,
        handoff: FrozenAgentStepHandoff | dict[str, FrozenAgentStepHandoff],
        source_sha256: str,
        owner: str,
        *,
        repair: AgentRepairContext | None = None,
        caller_input_file: CallerInputFileIdentity | None = None,
        repository_goon: bool = False,
        lock_artifacts: tuple[LockedExternalArtifact, ...] = (),
        source: SharedPlanSource | None = None,
    ) -> FleetPlanTarget:
        if self._content_store is None:
            raise FleetPlanInvalid("frozen Agent delivery requires an available content store")
        if caller_input_file is not None and plan.configuration is not None:
            plan = plan.model_copy(
                update={
                    "configuration": plan.configuration.with_caller_origins(
                        {}, caller_file_sha256=caller_input_file.sha256
                    )
                }
            )
        agents = [step for step in plan.steps if isinstance(step, ResolvedAgentStep)]
        deliveries = (
            handoff if isinstance(handoff, dict) else {agent.step_id: handoff for agent in agents}
        )
        references: list[AgentStepDeliveryReference] = []
        try:
            for agent in agents:
                packet = deliveries[agent.step_id].encode_prework_delivery()
                stored = self._content_store.put_frozen_bytes(packet, owner_repository_id=owner)
                references.append(
                    AgentStepDeliveryReference(
                        step_id=agent.step_id,
                        handoff_identity=agent.handoff_identity,
                        delivery_sha256=agent.delivery_sha256,
                        reference_id=stored.reference_id,
                    )
                )
            return FleetPlanTarget(
                kind="fleet_plan",
                plan=plan,
                source=source
                or (TeamGoonSource if repository_goon else PlanFileSource)(sha256=source_sha256),
                identity=ExecutionIdentity(
                    digest=build_execution_lock(plan, external_identities=lock_artifacts).digest
                ),
                lock_artifacts=lock_artifacts,
                agent_deliveries=tuple(references),
                agent_repair=repair,
                caller_input_file=caller_input_file,
            )
        except Exception as error:
            for reference in references:
                try:
                    self._content_store.release_reference(
                        reference.reference_id, owner_repository_id=owner
                    )
                except Exception as cleanup_error:
                    error.add_note(f"unadmitted Agent source cleanup failed: {cleanup_error}")
            raise

    def repair(
        self,
        plan_path: Path,
        *,
        budget: AgentRepairBudget,
        repository_id: str,
        inputs: dict[str, PlanInputValue],
        agent_repository_root: Path,
        agent_skill_root: Path,
        allowed_agent_acceptance: tuple[str, ...],
        fleet_run_id: str | None = None,
        lock_path: Path | None = None,
        extensions_path: Path | None = None,
        cancel_requested: Callable[[], bool] | None = None,
        requested_priority: SchedulingPriority = SchedulingPriority.NORMAL,
        review_write_roots: tuple[str, ...] | None = None,
        agent_connection: AgentConnection | None = None,
        native_agent: ResolvedNativeAgent | None = None,
        native_workspace: Path | None = None,
        caller_input_file: CallerInputFileIdentity | None = None,
        external_artifacts: tuple[tuple[str, bytes], ...] = (),
        repository_goon: bool = False,
        source_graph: FleetPlanSourceGraph | None = None,
        repository: Path | None = None,
        agent_selection: RunAgentSelection | None = None,
        root_expected_name: str | None = None,
        reviewed_source: SharedPlanSource | None = None,
        expected_plan_id: str | None = None,
        expected_execution_digest: str | None = None,
        expected_repository_instance_ids: dict[str, str] | None = None,
    ) -> AgentRepairResult:
        """Bounded caller-authorized repair, using only original separately admitted Runs.

        The original lock reviews the first source. Subsequent captures are permitted
        only by this frozen budget and unchanged original Plan/Skill configuration.
        No execution lock is rewritten and no independent runner/journal is created.
        """
        selected_repository = repository
        repair_id = fleet_run_id or new_fleet_run_id()
        if native_workspace is not None and native_agent is None:
            raise FleetPlanInvalid("A native workspace requires its frozen native selection")
        self.require_plan_ingress_preparation(repair_id)
        if agent_selection is not None:
            if (
                native_agent is not None
                or native_workspace is not None
                or agent_connection is not None
            ):
                raise FleetPlanInvalid(
                    "Shared Agent selection cannot be mixed with resolved provider authority"
                )
            if (
                reviewed_source is None
                or expected_plan_id is None
                or expected_execution_digest is None
                or expected_repository_instance_ids is None
                or selected_repository is None
                or root_expected_name is None
            ):
                raise FleetPlanInvalid(
                    "Reviewed Agent repair requires complete source and target authority"
                )
        elif any(
            value is not None
            for value in (
                reviewed_source,
                expected_plan_id,
                expected_execution_digest,
                expected_repository_instance_ids,
            )
        ):
            raise FleetPlanInvalid("Reviewed Agent repair requires its exact captured selection")
        if selected_repository is not None:
            from oh_goon.core.product_identity import repository_id as identify_context_repository

            if identify_context_repository(selected_repository) != repository_id:
                raise FleetPlanInvalid("Resolution repository differs from selected repair scope")
            if (
                native_workspace is not None
                and selected_repository.resolve() != native_workspace.resolve()
            ):
                raise FleetPlanInvalid(
                    "Native workspace differs from selected resolution repository"
                )
        if source_graph is not None:
            _selected_source_graph(self._resolver, plan_path, source_graph=source_graph)
        if repository_goon:
            from oh_goon.core.product_identity import repository_id as identify_source_repository

            if (
                plan_path.name != "goon.yml"
                or identify_source_repository(plan_path.parent) != repository_id
            ):
                raise FleetPlanInvalid(
                    "Repository Goon repair requires the exact selected repository root goon.yml"
                )
        if caller_input_file is not None:
            require_caller_file_fresh(caller_input_file)
        if not self._standard_local_fleet or self._content_store is None:
            raise FleetPlanInvalid(
                "bounded Agent repair requires explicit standard-local execution"
            )
        if native_agent is not None:
            if review_write_roots is None:
                raise FleetPlanInvalid("Native repair requires the complete review campaign")
            if native_workspace is None or agent_connection is not None:
                raise FleetPlanInvalid(
                    "Native repair requires one explicit workspace and its frozen native selection"
                )
            native_workspace = native_workspace.resolve(strict=True)
            # A campaign contains a later writable apply phase even though its
            # reasoning phases must retain read-only preflight semantics.
            require_native_workspace_runtime_separation(native_workspace, read_only_workspace=False)
            native_agent = self._prepare_native_agent(
                native_agent, native_workspace, read_only_workspace=True
            )
        content_store = self._content_store
        if type(budget) is not AgentRepairBudget:
            raise FleetPlanInvalid("repair requires explicit typed attempt and deadline limits")
        budget = AgentRepairBudget.model_validate(budget.model_dump())
        selected_inputs = dict(inputs)
        accepted = tuple(allowed_agent_acceptance)
        deadline_at = utc_now() + timedelta(seconds=budget.timeout_seconds)
        deadline = time.monotonic() + budget.timeout_seconds
        attempts: list[FleetRunDetail] = []
        campaign_revision: int | None = None
        campaign: AgentReviewRepairCampaign | None = None
        maximum_runs = budget.max_attempts
        current_selection: AgentCampaignUnstartedSelection | None = None
        redaction_steps: tuple[ResolvedPlanStep, ...] = ()
        passed_test_step_id: str | None = None

        def stop_requested() -> RepairStop | None:
            if cancel_requested is not None and cancel_requested():
                return RepairStop.CANCELLED
            if time.monotonic() >= deadline:
                return RepairStop.TIMED_OUT
            return None

        def commit_policy(
            reason: RepairStop | None,
            message: str = "",
            *,
            next_phase: str | None = None,
        ) -> None:
            nonlocal campaign_revision
            assert campaign_revision is not None
            retained, consumed, unresolved = self._runs.campaign_decision_inputs(repair_id)
            from oh_goon.integrations.fleet_plan_secret_redaction import redact_host_secret_text

            for step in redaction_steps:
                message = redact_host_secret_text(step, message)
            message = redact_diagnostic_text(message)
            if len(message) > MAX_AGENT_CAMPAIGN_MESSAGE_CHARS:
                message = TRUNCATED_AGENT_CAMPAIGN_MESSAGE
            # The predecessor and outcome bind this retry identity to one policy decision.
            import hashlib

            decision = AgentCampaignDecision(
                campaign_id=repair_id,
                expected_revision=campaign_revision,
                decision_id=hashlib.sha256(
                    f"{repair_id}:{campaign_revision}:{reason or next_phase}".encode()
                ).hexdigest(),
                phase_ordinal=len(retained.phases),
                remaining_runs=maximum_runs - len(retained.phases),
                consumed_runs=tuple(consumed),
                unresolved_run_ids=tuple(unresolved),
                candidate_identity=campaign.candidate_identity if campaign is not None else None,
                scope_identity=campaign.scope_identity if campaign is not None else None,
                next_phase=next_phase,
                next_step_ids=tuple(step.step_id for step in campaign.plan.steps)
                if reason is None and campaign is not None
                else tuple(step.step_id for step in plan.steps)
                if reason is None
                else (),
                unstarted_selection=current_selection if reason is not None else None,
                stop_reason=reason,
                passed_test_step_id=passed_test_step_id
                if reason is RepairStop.TEST_PASSED
                else None,
                message=message,
            )
            committed = self._runs.commit_agent_campaign_decision(decision)
            campaign_revision = committed.latest_revision

        def result(reason: RepairStop, message: str = "") -> AgentRepairResult:
            if campaign_revision is not None:
                if reason in {RepairStop.CONVERGED, RepairStop.TEST_PASSED}:
                    reason = stop_requested() or reason
                commit_policy(reason, message)
                return self._runs.agent_campaign_result(repair_id)
            return AgentRepairResult(
                repair_id,
                budget,
                reason,
                tuple(attempts),
                redact_diagnostic_text(message),
            )

        def discard_unadmitted_source(
            target: FleetPlanTarget, reason: RepairStop, message: str = ""
        ) -> AgentRepairResult:
            for delivery in target.agent_deliveries:
                try:
                    content_store.release_reference(
                        delivery.reference_id,
                        owner_repository_id=repository_id,
                    )
                except Exception as cleanup_error:
                    message = f"{message}; unadmitted source cleanup failed: {cleanup_error}"
            return result(reason, message)

        if (stop := stop_requested()) is not None:
            return result(stop)
        prepared_agent_plan = None
        if agent_selection is not None:
            assert selected_repository is not None and root_expected_name is not None
            try:
                prepared_agent_plan = self.prepare_agent_plan(
                    plan_path,
                    selection=agent_selection,
                    repository=selected_repository,
                    inputs=selected_inputs,
                    root_expected_name=root_expected_name,
                    require_review_phases=review_write_roots is not None,
                    cancel_requested=lambda: stop_requested() is not None,
                )
            except OperationCancelledError:
                return result(stop_requested() or RepairStop.CANCELLED)
            if (stop := stop_requested()) is not None:
                return result(stop)
            assert reviewed_source is not None
            assert expected_plan_id is not None and expected_execution_digest is not None
            assert expected_repository_instance_ids is not None
            self._require_reviewed_agent(
                prepared_agent_plan,
                reviewed_source,
                expected_plan_id,
                expected_execution_digest,
                {repository_id},
                expected_repository_instance_ids,
            )
            selected_inputs = prepared_agent_plan.capture_options["inputs"]
            agent_repository_root = prepared_agent_plan.capture_options["repository_root"]
            agent_skill_root = prepared_agent_plan.capture_options["skill_root"]
            accepted = prepared_agent_plan.capture_options["allowed_acceptance"]
            native_agent = prepared_agent_plan.capture_options.get("native_agent")
            if native_agent is not None:
                native_workspace = prepared_agent_plan.repository
                require_native_workspace_runtime_separation(
                    native_workspace, read_only_workspace=False
                )
        lock = (
            build_execution_lock(prepared_agent_plan.plan)
            if prepared_agent_plan is not None
            else load_execution_lock(lock_path or (plan_path.parent / LOCK_FILE_NAME))
        )
        capture_options: AgentCaptureOptions = dict(
            inputs=selected_inputs,
            repository_root=agent_repository_root,
            skill_root=agent_skill_root,
            allowed_acceptance=accepted,
            extensions_path=extensions_path,
            cancel_requested=lambda: stop_requested() is not None,
            connection=AgentConnection.model_validate(agent_connection.model_dump())
            if agent_connection is not None
            else None,
            locked_skills=lock.skill_sources,
            require_review_phases=review_write_roots is not None,
            source_graph=source_graph,
            native_agent=native_agent,
            repository=native_workspace if native_agent is not None else selected_repository,
            git_service=self._git_service if native_agent is not None else None,
        )
        if prepared_agent_plan is not None:
            capture_options = prepared_agent_plan.capture_options.copy()
            capture_options["cancel_requested"] = lambda: stop_requested() is not None
        target_handoff: FrozenAgentStepHandoff | dict[str, FrozenAgentStepHandoff]
        try:
            if review_write_roots is not None:
                plan, handoffs, source_sha = (
                    (
                        prepared_agent_plan.plan,
                        prepared_agent_plan.handoffs,
                        prepared_agent_plan.source_sha256,
                    )
                    if prepared_agent_plan is not None
                    else self._resolver.resolve_frozen_agents(plan_path, **capture_options)
                )
                campaign = AgentReviewRepairCampaign(
                    plan,
                    handoffs,
                    resolver=self._resolver,
                    plan_path=plan_path,
                    capture_options=capture_options,
                    passes=budget.max_attempts,
                    write_roots=review_write_roots,
                    lock_artifacts=lock.external,
                )
                target_handoff = handoffs
            else:
                plan, single_handoff, source_sha = self._resolver.resolve_frozen_agent(
                    plan_path,
                    inputs=selected_inputs,
                    repository_root=agent_repository_root,
                    skill_root=agent_skill_root,
                    allowed_acceptance=accepted,
                    extensions_path=extensions_path,
                    cancel_requested=capture_options["cancel_requested"],
                    locked_skills=lock.skill_sources,
                    source_graph=source_graph,
                )
                target_handoff = single_handoff
        except OperationCancelledError:
            return result(stop_requested() or RepairStop.CANCELLED)
        from oh_goon.core.product_identity import repository_id as identify_repository

        repository_handoff = (
            next(iter(target_handoff.values()))
            if isinstance(target_handoff, dict)
            else target_handoff
        )
        repository = (
            agent_repository_root
            / repository_handoff.source.repository_reference.removeprefix("local:")
        )
        if native_workspace is not None and repository.resolve(strict=True) != native_workspace:
            raise FleetPlanInvalid(
                "Captured repair repository differs from the explicit native workspace"
            )
        if identify_repository(repository) != repository_id:
            raise FleetPlanInvalid("repair capture differs from selected Fleet repository")
        steps = validate_agent_repair_plan(plan, single_handoff) if campaign is None else None
        original_lock = build_execution_lock(plan, external_artifacts=external_artifacts)
        verify_execution_lock(original_lock, lock)
        maximum_runs = (
            budget.max_attempts if campaign is None else 4 * (budget.max_attempts - 1) + 2
        )
        redaction_steps = plan.steps
        original_digest = original_lock.digest
        if campaign is not None:
            plan = campaign.plan
        retained_intent = self._runs.begin_agent_campaign(
            AgentCampaignIntent(
                campaign_id=repair_id,
                repository_id=repository_id,
                original_execution_digest=original_digest,
                budget=budget,
                maximum_runs=maximum_runs,
                deadline_at=deadline_at,
                initial_phase=campaign.phase if campaign is not None else "repair",
                initial_step_ids=tuple(step.step_id for step in plan.steps),
                candidate_identity=campaign.candidate_identity if campaign is not None else None,
                scope_identity=campaign.scope_identity if campaign is not None else None,
                write_roots=campaign.write_roots if campaign is not None else (),
            )
        )
        campaign_revision = retained_intent.latest_revision
        from oh_goon.runtime.workspaces import RepositoryWorkspaceAuthority

        with RepositoryWorkspaceAuthority.pin_repository_subject(
            repository
        ) as require_same_subject:
            for ordinal in range(1, maximum_runs + 1):
                if (stop := stop_requested()) is not None:
                    return result(stop)
                try:
                    require_same_subject()
                except (OSError, FleetPlanInvalid) as exc:
                    return result(RepairStop.SOURCE_CHANGED, str(exc))
                repair_context = AgentRepairContext.model_validate(
                    {
                        "repair_id": repair_id,
                        "campaign_revision": campaign_revision,
                        "attempt": ordinal,
                        "max_attempts": maximum_runs,
                        **(
                            {
                                "phase": campaign.phase,
                                "review_pass": campaign.review_pass,
                                "original_execution_digest": campaign.original_digest,
                                "candidate_identity": campaign.candidate_identity,
                                "scope_identity": campaign.scope_identity,
                                "write_roots": campaign.write_roots,
                            }
                            if campaign is not None
                            else {}
                        ),
                        "limits": FleetPlanRunLimits(
                            deadline_at=deadline_at,
                            step_timeout_seconds=budget.step_timeout_seconds,
                        ),
                        "previous_run_id": attempts[-1].run.fleet_run_id if attempts else None,
                    }
                )
                try:
                    if caller_input_file is not None:
                        require_caller_file_fresh(caller_input_file)
                    target = self._agent_target(
                        plan,
                        target_handoff,
                        source_sha,
                        repository_id,
                        repair=repair_context,
                        caller_input_file=caller_input_file,
                        repository_goon=repository_goon,
                        lock_artifacts=lock.external,
                        source=reviewed_source,
                    )
                except (OSError, FleetPlanInvalid) as exc:
                    return result(RepairStop.EXECUTION_UNRESOLVED, str(exc))
                try:
                    require_same_subject()
                except (OSError, FleetPlanInvalid) as exc:
                    return discard_unadmitted_source(target, RepairStop.SOURCE_CHANGED, str(exc))
                try:
                    instances = self._runs.repository_instance_ids_for_prepared_plan(
                        target, repository_ids={repository_id}
                    )
                except Exception as exc:
                    return discard_unadmitted_source(
                        target, RepairStop.EXECUTION_UNRESOLVED, str(exc)
                    )
                try:
                    require_same_subject()
                except (OSError, FleetPlanInvalid) as exc:
                    return discard_unadmitted_source(target, RepairStop.SOURCE_CHANGED, str(exc))
                if (stop := stop_requested()) is not None:
                    return discard_unadmitted_source(target, stop)
                selected_run_id = repair_id if ordinal == 1 else new_fleet_run_id()
                current_selection = AgentCampaignUnstartedSelection(
                    selected_run_id=selected_run_id,
                    phase_ordinal=ordinal,
                    phase=campaign.phase if campaign is not None else "repair",
                    selected_step_ids=tuple(step.step_id for step in target.plan.steps),
                )
                try:
                    started = self._runs.start_prepared_plan(
                        target,
                        repository_ids={repository_id},
                        fleet_run_id=selected_run_id,
                        expected_repository_instance_ids=(
                            expected_repository_instance_ids
                            if ordinal == 1 and expected_repository_instance_ids is not None
                            else instances
                        ),
                        requested_priority=requested_priority,
                        source=FleetRunSource.SHARED_RUN
                        if reviewed_source is not None
                        else (FleetRunSource.CICD if repository_goon else FleetRunSource.MANUAL),
                    )
                    if started.fleet_run_id != selected_run_id or started.target != target:
                        raise StateConflict(
                            "Admission response differs from its exact selected Run/target"
                        )
                except Exception as exc:
                    # Missing observation is not proof of absent admission. The
                    # original Journal's atomic phase linkage owns that fact.
                    retained = self._runs.campaign_journal(repair_id)
                    linked = next(
                        (
                            phase
                            for phase in retained.phases
                            if phase.fleet_run_id == selected_run_id
                        ),
                        None,
                    )
                    if linked is None:
                        return discard_unadmitted_source(
                            target, RepairStop.EXECUTION_UNRESOLVED, str(exc)
                        )
                    campaign_revision = retained.latest_revision
                    current_selection = None
                    if (
                        linked.context != repair_context
                        or linked.execution_digest != target.execution_digest
                    ):
                        return result(
                            RepairStop.EXECUTION_UNRESOLVED,
                            "Admission linkage conflicts with the exact selected target",
                        )
                    try:
                        # Exact canonical linkage is sufficient to cancel the
                        # selected child even when its response/readback was lost.
                        try:
                            self._runs.cancel(selected_run_id)
                        except InvalidRunTransition:
                            pass
                        observed = self._runs.wait_for_terminal(selected_run_id)
                        if (
                            observed.run.fleet_run_id != selected_run_id
                            or observed.run.target != target
                        ):
                            raise StateConflict(
                                "Admitted Run readback conflicts with its selected target"
                            )
                        attempts.append(observed)
                    except Exception as cleanup_error:
                        return result(
                            RepairStop.EXECUTION_UNRESOLVED,
                            f"{exc}; native cleanup/readback also failed: {cleanup_error}",
                        )
                    return result(RepairStop.EXECUTION_UNRESOLVED, str(exc))
                current_selection = None
                campaign_revision = self._runs.campaign_journal(repair_id).latest_revision

                def cancel_current(started: FleetRunState = started) -> None:
                    try:
                        self._runs.cancel(started.fleet_run_id)
                    except InvalidRunTransition:
                        if not self._runs.get(started.fleet_run_id).run.status.terminal:
                            raise

                forced_stop: RepairStop | None = None
                try:
                    while True:
                        if forced_stop is None and (stop := stop_requested()) is not None:
                            forced_stop = stop
                            cancel_current()
                        try:
                            detail = self._runs.wait_for_terminal(started.fleet_run_id, timeout=0.1)
                            if (
                                detail.run.fleet_run_id != started.fleet_run_id
                                or detail.run.target != target
                            ):
                                raise StateConflict(
                                    "repair result does not match its admitted Run/source"
                                )
                            break
                        except TimeoutError:
                            continue
                except KeyboardInterrupt:
                    cancel_current()
                    detail = self._runs.wait_for_terminal(started.fleet_run_id)
                    forced_stop = RepairStop.CANCELLED
                except Exception as exc:
                    # Never turn missing worker/terminal evidence into another repair.
                    # Keep the admitted ID even if durable readback itself is unavailable.
                    try:
                        observed = self._runs.get(started.fleet_run_id)
                        if not observed.run.status.terminal:
                            cancel_current()
                            observed = self._runs.wait_for_terminal(started.fleet_run_id)
                        attempts.append(observed)
                    except Exception as cleanup_error:
                        return result(
                            RepairStop.EXECUTION_UNRESOLVED,
                            f"{exc}; native cleanup/readback also failed: {cleanup_error}",
                        )
                    return result(RepairStop.EXECUTION_UNRESOLVED, str(exc))
                attempts.append(detail)
                if forced_stop is not None:
                    return result(forced_stop)
                if campaign is not None:
                    try:
                        require_same_subject()
                        decision = campaign.advance(detail)
                        require_same_subject()
                    except OperationCancelledError:
                        return result(stop_requested() or RepairStop.CANCELLED)
                    except (OSError, FleetPlanInvalid, ValueError) as exc:
                        return result(RepairStop.SOURCE_CHANGED, str(exc))
                    if decision is not None:
                        return result(decision, campaign.message)
                    commit_policy(None, next_phase=campaign.phase)
                    plan, target_handoff = campaign.plan, campaign.handoffs
                    continue
                assert steps is not None
                repair_decision = repair_attempt_decision(detail, steps)
                if isinstance(repair_decision, PassedRepairTest):
                    passed_test_step_id = repair_decision.step_id
                    return result(RepairStop.TEST_PASSED)
                if repair_decision is not None:
                    return result(repair_decision)
                if ordinal == budget.max_attempts:
                    return result(RepairStop.ATTEMPTS_EXHAUSTED)
                try:
                    require_same_subject()

                    def capture_cancelled() -> bool:
                        require_same_subject()
                        return stop_requested() is not None

                    single_handoff = self._resolver.recapture_agent_step_handoff(
                        plan_path,
                        previous_handoff=single_handoff,
                        expected_previous_handoff_identity=single_handoff.handoff_identity,
                        inputs=selected_inputs,
                        repository_root=agent_repository_root,
                        skill_root=agent_skill_root,
                        allowed_acceptance=accepted,
                        cancel_requested=capture_cancelled,
                        locked_skills=lock.skill_sources,
                        source_graph=source_graph,
                        locked_goons=plan.goon_sources,
                    )
                    require_same_subject()
                except OperationCancelledError:
                    return result(stop_requested() or RepairStop.CANCELLED)
                except (OSError, FleetPlanInvalid) as exc:
                    return result(RepairStop.SOURCE_CHANGED, str(exc))
                # All non-source semantics stay frozen. Rebuild only the Agent's source
                # binding through its original resolver and the canonical Plan constructor.
                from oh_goon.services.fleet_plan_step_resolution import (
                    resolve_delivered_agent_step,
                )

                rebound = resolve_delivered_agent_step(single_handoff).model_copy(
                    update={
                        "step_id": steps.agent.step_id,
                        "source_step_id": single_handoff.step_id,
                        "feedback_reference": steps.agent.feedback_reference,
                        "when": steps.agent.when,
                    }
                )
                plan = ResolvedFleetPlan.create(
                    name=plan.name,
                    description=plan.description,
                    execution=plan.execution,
                    steps=(steps.test, rebound, steps.apply, steps.retest),
                    requirements=plan.requirements,
                    skill_sources=plan.skill_sources,
                    goon_sources=plan.goon_sources,
                    imports=plan.imports,
                    step_sources=plan.step_sources,
                )
                steps = validate_agent_repair_plan(plan, single_handoff)
                target_handoff = single_handoff
                commit_policy(None, next_phase="repair")
        raise AssertionError("validated repair budget did not select a terminal decision")

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
            plan_path,
            expected_execution_digest=expected_execution_digest,
            inputs=inputs,
            source=source,
            expected_plan_id=expected_plan_id,
            root_expected_name=root_expected_name,
        )
        return self._runs.start_prepared_plan(
            target,
            repository_ids=repository_ids,
            all_repositories=False,
            fleet_run_id=fleet_run_id,
            requested_priority=requested_priority,
            expected_repository_instance_ids=expected_repository_instance_ids,
            source=FleetRunSource.SHARED_RUN
            if isinstance(target.source, SharedPlanSource)
            else FleetRunSource.MANUAL,
        )

    def repository_instance_ids_for_target(
        self, target: FleetPlanTarget, *, repository_ids: set[str]
    ) -> dict[str, str]:
        """Snapshot current repository instances for one prepared Fleet Plan target."""
        return self._runs.repository_instance_ids_for_prepared_plan(
            target, repository_ids=repository_ids
        )

    def project_source_transport_for_target(
        self,
        target: FleetPlanTarget,
        *,
        repository_ids: set[str],
        expected_repository_instance_ids: dict[str, str] | None = None,
    ) -> AbstractContextManager[tuple[FleetPlanTarget, dict[str, str]]]:
        """Retain already-resolved Schedule source through the original Fleet owner."""
        return self._runs.project_source_transport_for_prepared_plan(
            target,
            repository_ids=repository_ids,
            expected_repository_instance_ids=expected_repository_instance_ids,
        )

    def _prepare_scheduled_target(
        self,
        plan_path: Path,
        *,
        extensions_path: Path | None,
        inputs: dict[str, PlanInputValue] | None,
        caller_input_file: CallerInputFileIdentity | None,
        caller_input_origins: Mapping[str, PlanCallerOrigin] | None = None,
        external_artifacts: tuple[tuple[str, bytes], ...] = (),
        lock_path: Path | None = None,
        repository_goon: bool = False,
        source_graph: FleetPlanSourceGraph | None = None,
        repository: Path | None = None,
    ) -> FleetPlanTarget:
        """Freeze caller-selected values once; future occurrences own no caller file.

        The existing resolver validates values. The only observed file is the
        same CLI-selected source, proven fresh before/after target construction.
        """
        if caller_input_file is not None:
            require_caller_file_fresh(caller_input_file)
        target = self.prepare_execution_target(
            plan_path,
            extensions_path=extensions_path,
            inputs=inputs,
            caller_input_file=caller_input_file,
            caller_input_origins=caller_input_origins,
            external_artifacts=external_artifacts,
            lock_path=lock_path,
            repository_goon=repository_goon,
            source_graph=source_graph,
            repository=repository,
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
        sealed = FleetPlanTarget.model_validate(
            {
                **target.model_dump(exclude={"plan", "caller_input_file", "scheduled_caller_file"}),
                "plan": target.plan,
                "scheduled_caller_file": frozen.model_dump(),
            }
        )
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
        caller_input_origins: Mapping[str, PlanCallerOrigin] | None = None,
        external_artifacts: tuple[tuple[str, bytes], ...] = (),
        lock_path: Path | None = None,
        repository_goon: bool = False,
        source_graph: FleetPlanSourceGraph | None = None,
        repository: Path | None = None,
    ) -> ScheduleDefinition:
        """Resolve once, then persist one Schedule target set for the exact Plan."""
        if repository is not None:
            from oh_goon.core.product_identity import repository_id as identify_context_repository

            if repository_ids != {identify_context_repository(repository)}:
                raise FleetPlanInvalid("Resolution repository differs from selected Schedule scope")
        target = self._prepare_scheduled_target(
            plan_path,
            extensions_path=extensions_path,
            inputs=inputs,
            caller_input_file=caller_input_file,
            caller_input_origins=caller_input_origins,
            external_artifacts=external_artifacts,
            lock_path=lock_path,
            repository_goon=repository_goon,
            source_graph=source_graph,
            repository=repository,
        )
        package_sources = any(getattr(step, "project_source", None) for step in target.plan.steps)
        if package_sources:
            try:
                current = self._schedules.definition(schedule_id)
            except ScheduleNotFound:
                current = None
            if current is not None and current.repository_ids == tuple(sorted(repository_ids)):
                target = with_retained_project_deliveries(target, current.target)
        transport = (
            self.project_source_transport_for_target(target, repository_ids=repository_ids)
            if package_sources
            else nullcontext(
                (
                    target,
                    self.repository_instance_ids_for_target(target, repository_ids=repository_ids),
                )
            )
        )
        with transport as (target, repository_instance_ids):
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
        caller_input_origins: Mapping[str, PlanCallerOrigin] | None = None,
        external_artifacts: tuple[tuple[str, bytes], ...] = (),
        lock_path: Path | None = None,
        repository_goon: bool = False,
        source_graph: FleetPlanSourceGraph | None = None,
        repository: Path | None = None,
    ) -> ScheduleDefinition:
        """CAS an existing Schedule target set onto a newly resolved immutable Plan."""
        current = self._schedules.definition(schedule_id)
        if repository is not None:
            from oh_goon.core.product_identity import repository_id as identify_context_repository

            if repository_ids != {identify_context_repository(repository)}:
                raise FleetPlanInvalid("Resolution repository differs from selected Schedule scope")
        target = self._prepare_scheduled_target(
            plan_path,
            extensions_path=extensions_path,
            inputs=inputs,
            caller_input_file=caller_input_file,
            caller_input_origins=caller_input_origins,
            external_artifacts=external_artifacts,
            lock_path=lock_path,
            repository_goon=repository_goon,
            source_graph=source_graph,
            repository=repository,
        )
        package_sources = any(getattr(step, "project_source", None) for step in target.plan.steps)
        if package_sources and current.repository_ids == tuple(sorted(repository_ids)):
            target = with_retained_project_deliveries(target, current.target)
        transport = (
            self.project_source_transport_for_target(target, repository_ids=repository_ids)
            if package_sources
            else nullcontext(
                (
                    target,
                    self.repository_instance_ids_for_target(target, repository_ids=repository_ids),
                )
            )
        )
        with transport as (target, repository_instance_ids):
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


def _selected_source_graph(
    resolver: FleetPlanResolver,
    plan_path: Path,
    *,
    source_graph: FleetPlanSourceGraph | None,
    root_expected_name: str | None = None,
) -> FleetPlanSourceGraph:
    if source_graph is None:
        return resolver.analyze_source(plan_path, root_expected_name=root_expected_name)
    if source_graph.root.path != navigation_path(plan_path):
        raise FleetPlanInvalid("Captured source graph belongs to a different selected source")
    if root_expected_name is not None and source_graph.root.compiled.name != root_expected_name:
        raise FleetPlanInvalid("Captured source graph differs from the selected root identity")
    return source_graph


def _generate_execution_lock(
    resolver: FleetPlanResolver,
    plan_path: Path,
    *,
    extensions_path: Path | None,
    inputs: dict[str, PlanInputValue] | None,
    output: Path | None,
    external_artifacts: tuple[tuple[str, bytes], ...] = (),
    caller_input_file: CallerInputFileIdentity | None = None,
    source_graph: FleetPlanSourceGraph | None = None,
    repository: Path | None = None,
) -> ExecutionLock:
    if caller_input_file is not None:
        require_caller_file_fresh(caller_input_file)
    graph = _selected_source_graph(resolver, plan_path, source_graph=source_graph)
    plan = resolver.resolve_source_graph(
        graph, extensions_path=extensions_path, inputs=inputs, repository=repository
    )
    lock = build_execution_lock(plan, external_artifacts=external_artifacts)
    destination = output or (plan_path.parent / LOCK_FILE_NAME)
    if caller_input_file is not None:
        require_caller_file_fresh(caller_input_file)
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
    external_artifacts: tuple[tuple[str, bytes], ...] = (),
    source_graph: FleetPlanSourceGraph | None = None,
    repository: Path | None = None,
) -> tuple[ResolvedFleetPlan, str, ExecutionLock]:
    graph = _selected_source_graph(
        resolver, plan_path, source_graph=source_graph, root_expected_name=root_expected_name
    )
    plan = resolver.resolve_source_graph(
        graph, extensions_path=extensions_path, inputs=inputs, repository=repository
    )
    actual = load_execution_lock(lock_path or (plan_path.parent / LOCK_FILE_NAME))
    expected = build_execution_lock(plan, external_artifacts=external_artifacts)
    verify_execution_lock(expected, actual)
    if graph.root.source_sha256 is None:
        raise FleetPlanInvalid("Fleet plan source fingerprint is unavailable")
    return plan, graph.root.source_sha256, actual


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
        raise FleetPlanInvalid(
            "Fleet plan execution identity changed after preview; preview the exact plan again before execution"
        )
    if graph.root.source_sha256 is None:
        raise FleetPlanInvalid("Fleet plan source fingerprint is unavailable")
    source_sha256 = graph.root.source_sha256
    if source is not None and source.sha256 != source_sha256:
        raise FleetPlanInvalid(
            "Fleet plan source identity does not match the exact bytes resolved for execution"
        )
    _require_expected_plan_identity(plan, expected_plan_id)
    return FleetPlanTarget(
        kind="fleet_plan",
        plan=plan,
        source=source or PlanFileSource(sha256=source_sha256),
        identity=ExecutionIdentity(digest=lock.digest),
    )


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
    caller_input_origins: Mapping[str, PlanCallerOrigin] | None = None,
    external_artifacts: tuple[tuple[str, bytes], ...] = (),
    repository_goon: bool = False,
    source_graph: FleetPlanSourceGraph | None = None,
    repository: Path | None = None,
) -> FleetPlanTarget:
    """Resolve and verify one locked source into the only admissible Plan target shape."""
    if repository_goon and (
        plan_path.name != "goon.yml"
        or (source is not None and not isinstance(source, TeamGoonSource))
    ):
        raise FleetPlanInvalid("Repository Goon source requires its exact root goon.yml authority")
    if caller_input_file is not None:
        require_caller_file_fresh(caller_input_file)
    plan, source_sha256, verified_lock = _resolve_locked_execution(
        resolver,
        plan_path,
        extensions_path=extensions_path,
        inputs=inputs,
        root_expected_name=root_expected_name,
        lock_path=lock_path,
        external_artifacts=external_artifacts,
        source_graph=source_graph,
        repository=repository,
    )
    if source is not None and source.sha256 != source_sha256:
        raise FleetPlanInvalid(
            "Fleet plan source identity does not match the exact bytes resolved for execution"
        )
    _require_expected_plan_identity(plan, expected_plan_id)
    if plan.configuration is not None and (caller_input_origins or caller_input_file is not None):
        plan = plan.model_copy(
            update={
                "configuration": plan.configuration.with_caller_origins(
                    caller_input_origins or {},
                    caller_file_sha256=caller_input_file.sha256 if caller_input_file else None,
                )
            }
        )
    if caller_input_file is not None:
        require_caller_file_fresh(caller_input_file)
    return FleetPlanTarget(
        kind="fleet_plan",
        plan=plan,
        source=source
        or (TeamGoonSource if repository_goon else PlanFileSource)(sha256=source_sha256),
        identity=ExecutionIdentity(digest=verified_lock.digest),
        lock_artifacts=verified_lock.external,
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
