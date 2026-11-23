"""Compose the existing Fleet execution owner from one resolved Run handoff.

This is the worker-side portion of application wiring. It owns neither ingress,
startup recovery nor scheduling, and is called only after SQLite custody claim.
"""

from __future__ import annotations

import tempfile
from copy import deepcopy
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, cast

from pydantic import ConfigDict, Field, JsonValue, model_validator

from oh_goon._application_foundation import _build_capability_runtime
from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.agent_providers import ResolvedNativeAgent
from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.models.run_resources import RunResourceEnvelopeEvidence
from oh_goon.core.path_layouts import (
    ApplicationStatePathLayout,
    require_pi_session_scratch_separation,
)
from oh_goon.core.ports.diagnostics import DiagnosticEmitter
from oh_goon.engine.command import CommandTerminal
from oh_goon.engine.fleet_extensions import FleetExtensionStore
from oh_goon.engine.fleet_tools import FleetToolStore
from oh_goon.engine.git import GitService
from oh_goon.integrations.fleet_plan_process_isolation import (
    NativeProfileCommandRunner,
    ProcessIsolationBackend,
    native_profile_command_runner,
)
from oh_goon.integrations.hashmarks import require_loaded_hashmarks_runtime
from oh_goon.integrations.run_resource_envelope import ResolvedRunResourceScope
from oh_goon.runtime._executor_configuration import (
    WorkerExecutorPoolConfiguration,
    WorkerIsolationConfiguration,
    capture_worker_executor_configuration,
    capture_worker_isolation_configuration,
    restore_worker_executor_configuration,
    restore_worker_isolation_configuration,
)
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.effect_attempts import RepositoryEffectAttemptStore
from oh_goon.runtime.execution import ExecutionCoordinator, ExecutionTicket
from oh_goon.runtime.game_tape_live_output import GameTapeLiveOutputStore
from oh_goon.runtime.journal import Journal
from oh_goon.runtime.workspaces import RepositoryWorkspaceAuthority
from oh_goon.services.fleet_plan_candidate import (
    capture_workspace_control_git_environment,
    restore_workspace_control_git,
)
from oh_goon.services.fleet_run_execution import FleetRunExecution
from oh_goon.services.fleet_run_lifecycle import FleetRunLifecycle
from oh_goon.services.fleet_target_scheduler import FleetTargetScheduler
from oh_goon.services.fleet_worker_authority import (
    FleetWorkerAuthority,
    capture_fleet_worker_authority,
    restore_fleet_worker_authority,
)
from oh_goon.services.repository_effect_execution import RepositoryEffectExecution
from oh_goon.services.repository_effect_fence import RepositoryEffectFence
from oh_goon.services.repository_effect_settlement import RepositoryEffectSettlement

if TYPE_CHECKING:
    from oh_goon._application_foundation import RuntimeFoundation


class WorkerGitConfiguration(ImmutableModel):
    executable: str = Field(min_length=1)
    default_timeout_seconds: float
    native_repository: Path | None = None
    native_agent: ResolvedNativeAgent | None = Field(default=None, repr=False)
    native_observation: bool = False
    metadata_only: bool = Field(default=False, strict=True)
    metadata_environment: dict[str, str] | None = Field(
        default=None, exclude_if=lambda value: value is None, repr=False
    )

    @model_validator(mode="after")
    def require_native_binding(self) -> WorkerGitConfiguration:
        if (self.native_repository is None) != (self.native_agent is None):
            raise ValueError("Native Git requires one complete workspace/profile binding")
        if self.native_repository is not None and (
            not self.native_repository.is_absolute() or ".." in self.native_repository.parts
        ):
            raise ValueError("Native Git requires its resolved absolute workspace")
        expected_observation = (
            self.native_agent is not None and self.native_agent.profile.backend == "bubblewrap"
        )
        if self.native_observation is not expected_observation:
            raise ValueError("Native Git observation differs from its selected backend")
        if self.metadata_environment is not None:
            if self.native_agent is not None or not self.metadata_only:
                raise ValueError("Metadata Git cannot inherit a payload execution binding")
            restore_workspace_control_git(
                executable=self.executable,
                default_timeout_seconds=self.default_timeout_seconds,
                environment=self.metadata_environment,
            )
        return self

    @classmethod
    def capture(
        cls, git: GitService | None, *, isolation: ProcessIsolationBackend | None = None
    ) -> WorkerGitConfiguration:
        if type(git) is not GitService:
            raise FleetPlanInvalid(
                "Run worker requires native Git configuration without injected callbacks"
            )
        runner = git.command_runner
        captured_metadata = capture_workspace_control_git_environment(git)
        if captured_metadata is not None:
            return cls(
                executable=git.executable,
                default_timeout_seconds=git.default_timeout_seconds,
                metadata_only=True,
                metadata_environment=captured_metadata,
            )
        if runner is None and git.observation_runner is None:
            return cls(
                executable=git.executable,
                default_timeout_seconds=git.default_timeout_seconds,
                metadata_only=git.metadata_only,
            )
        if (
            type(runner) is not NativeProfileCommandRunner
            or runner.workspace_pin is not RepositoryWorkspaceAuthority.pin_repository_subject
            or runner.termination_requested is not None
            or runner.repair_context is not None
            or runner.backend is not isolation
            or runner.workspace_materialization is not None
            or runner.workspace_directory_fd is not None
            or runner.require_candidate_current is not None
        ):
            raise FleetPlanInvalid(
                "Run worker requires native Git configuration without injected callbacks"
            )
        observation = git.observation_runner
        read_only = runner.native_agent.profile.backend == "bubblewrap"
        if read_only:
            if (
                type(observation) is not partial
                or observation.func is not runner
                or observation.args
                or set(observation.keywords) != {"read_only_workspace"}
                or observation.keywords["read_only_workspace"] is not True
            ):
                raise FleetPlanInvalid("Run worker requires the original scoped Git observation")
        elif observation is not None:
            raise FleetPlanInvalid("Run worker requires the original scoped Git observation")
        return cls(
            executable=git.executable,
            default_timeout_seconds=git.default_timeout_seconds,
            native_repository=runner.repository,
            native_agent=deepcopy(runner.native_agent),
            native_observation=read_only,
            metadata_only=git.metadata_only,
        )

    def restore(self, *, isolation: ProcessIsolationBackend | None = None) -> GitService:
        if self.metadata_environment is not None:
            return restore_workspace_control_git(
                executable=self.executable,
                default_timeout_seconds=self.default_timeout_seconds,
                environment=self.metadata_environment,
            )
        if self.native_agent is not None:
            assert self.native_repository is not None
            runner = native_profile_command_runner(
                self.native_repository,
                self.native_agent,
                isolation,
                RepositoryWorkspaceAuthority.pin_repository_subject,
            )
            return GitService(
                executable=self.executable,
                default_timeout_seconds=self.default_timeout_seconds,
                command_runner=runner,
                observation_runner=partial(runner, read_only_workspace=True)
                if self.native_observation
                else None,
                metadata_only=self.metadata_only,
            )
        return GitService(
            executable=self.executable,
            default_timeout_seconds=self.default_timeout_seconds,
            metadata_only=self.metadata_only,
        )


class RunWorkerConfiguration(ImmutableModel):
    """Protected configuration for exactly one admitted Run; contains no live resources."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    fleet_run_id: str = Field(min_length=1)
    hashmarks_implementation_identity: str = Field(min_length=1)
    extension_root: Path
    tool_root: Path
    content_store_root: Path
    run_parent: Path
    git: WorkerGitConfiguration
    executor_configuration: WorkerExecutorPoolConfiguration | None = Field(repr=False)
    standard_process_isolation: WorkerIsolationConfiguration | None = Field(repr=False)
    fleet_authority: FleetWorkerAuthority | None = Field(repr=False)
    standard_local_fleet: bool
    require_authenticated_fleet: bool
    require_compiled_fleet_contract: bool
    run_resource_scope: dict[str, JsonValue] | None = Field(repr=False)
    run_resource_envelope: RunResourceEnvelopeEvidence | None
    deferred_worker_resource_scope: dict[str, JsonValue] | None = Field(
        default=None, exclude_if=lambda value: value is None, repr=False
    )

    @model_validator(mode="after")
    def require_complete_binding(self) -> RunWorkerConfiguration:
        if any(
            not path.is_absolute() or ".." in path.parts
            for path in (
                self.extension_root,
                self.tool_root,
                self.content_store_root,
                self.run_parent,
            )
        ):
            raise ValueError("Run worker requires resolved absolute store/workspace roots")
        if self.standard_local_fleet and (
            self.require_authenticated_fleet
            or self.require_compiled_fleet_contract
            or self.fleet_authority is not None
        ):
            raise ValueError("Ordinary Run worker cannot inherit strict enrollment")
        if self.require_compiled_fleet_contract and not self.require_authenticated_fleet:
            raise ValueError("Compiled Fleet contract requires authenticated admission")
        if (self.run_resource_scope is None) != (self.run_resource_envelope is None):
            raise ValueError("Run worker resources require one complete observed binding")
        if self.run_resource_scope is not None:
            scope = ResolvedRunResourceScope.from_payload(self.run_resource_scope)
            if not self.standard_local_fleet or scope.fleet_run_id != self.fleet_run_id:
                raise ValueError("Run worker resource scope differs from selected Run")
            if self.run_resource_envelope is None or (
                self.run_resource_envelope.fleet_run_id != self.fleet_run_id
            ):
                raise ValueError("Run worker resource evidence differs from selected Run")
        if self.deferred_worker_resource_scope is not None:
            from oh_goon.integrations.run_resource_envelope import DeferredWorkerResourceScope

            deferred = DeferredWorkerResourceScope.from_payload(self.deferred_worker_resource_scope)
            if (
                not self.standard_local_fleet
                or self.run_resource_scope is not None
                or self.run_resource_envelope is not None
                or deferred.admission.fleet_run_id != self.fleet_run_id
            ):
                raise ValueError("Deferred worker resources differ from original Run configuration")
        return self


def capture_runtime_worker_configuration(
    foundation: RuntimeFoundation, fleet_run_id: str
) -> RunWorkerConfiguration:
    """Capture the already resolved parent inputs, including only this Run's enrollment."""
    paths = ApplicationStatePathLayout.from_database(foundation.database.path)
    deferred_payload: dict[str, JsonValue] | None = None
    if foundation.run_capacity is not None:
        from oh_goon.integrations.run_resource_envelope import prepare_worker_resource_scope

        if foundation.terminal is not None:
            raise FleetPlanInvalid("Concurrent Run workers require headless execution")
        deferred_payload = cast(
            dict[str, JsonValue],
            prepare_worker_resource_scope(fleet_run_id, foundation.run_capacity).to_payload(),
        )
    return RunWorkerConfiguration(
        fleet_run_id=fleet_run_id,
        hashmarks_implementation_identity=require_loaded_hashmarks_runtime(),
        extension_root=foundation.extension_store.root,
        tool_root=foundation.tool_store.root,
        content_store_root=foundation.content_store.root,
        run_parent=paths.run_workspaces,
        git=WorkerGitConfiguration.capture(
            foundation.git_service, isolation=foundation.standard_process_isolation
        ),
        executor_configuration=capture_worker_executor_configuration(
            foundation.executor_configuration
        ),
        standard_process_isolation=(
            capture_worker_isolation_configuration(foundation.standard_process_isolation)
            if foundation.standard_process_isolation is not None
            else None
        ),
        fleet_authority=capture_fleet_worker_authority(
            foundation.fleet_provisioner,
            fleet_run_id,
            foundation.executor_configuration,
            expected_inventory_identity=foundation.executor_inventory_identity,
        ),
        standard_local_fleet=foundation.standard_local_fleet,
        require_authenticated_fleet=foundation.require_authenticated_fleet,
        require_compiled_fleet_contract=foundation.require_compiled_fleet_contract,
        run_resource_scope=(
            cast(dict[str, JsonValue], foundation.run_resource_scope.to_payload())
            if foundation.run_resource_scope is not None
            else None
        ),
        run_resource_envelope=foundation.run_resource_envelope,
        deferred_worker_resource_scope=deferred_payload,
    )


def build_claimed_run_execution(
    database: LocalDatabase,
    claimed_ticket: ExecutionTicket,
    configuration: RunWorkerConfiguration,
    *,
    terminal: CommandTerminal | None,
    live_output: GameTapeLiveOutputStore,
    diagnostics: DiagnosticEmitter,
    worker_resource_envelope: RunResourceEnvelopeEvidence | None = None,
) -> FleetRunExecution:
    """Build only the existing execution graph after the canonical worker claim."""
    if claimed_ticket.require_fleet_run_id() != configuration.fleet_run_id:
        raise FleetPlanInvalid("Run worker configuration differs from claimed Run")
    execution = ExecutionCoordinator(database)
    execution.require_claimed_fleet_worker(claimed_ticket)
    state_paths = ApplicationStatePathLayout.from_database(database.path)
    require_pi_session_scratch_separation(
        Path(tempfile.gettempdir()).resolve(), state_paths.pi_sessions
    )
    resource_scope = (
        ResolvedRunResourceScope.from_payload(configuration.run_resource_scope)
        if configuration.run_resource_scope is not None
        else None
    )
    resource_envelope = configuration.run_resource_envelope
    if configuration.deferred_worker_resource_scope is not None:
        from oh_goon.integrations.run_resource_envelope import DeferredWorkerResourceScope

        deferred = DeferredWorkerResourceScope.from_payload(
            configuration.deferred_worker_resource_scope
        )
        if (
            worker_resource_envelope is None
            or worker_resource_envelope.subject != "worker"
            or worker_resource_envelope.fleet_run_id != configuration.fleet_run_id
            or worker_resource_envelope.unit_name != deferred.admission.worker_unit_name
            or worker_resource_envelope.resources != deferred.admission.policy.worker_resources
        ):
            raise FleetPlanInvalid("Claimed worker lacks its actual native resource envelope")
        reservation = execution.activate(claimed_ticket).observe(
            lambda connection: execution.worker_resource_reservation_in(
                connection, configuration.fleet_run_id
            )
        )
        if (
            reservation is None
            or reservation.admission != deferred.admission
            or reservation.envelope != worker_resource_envelope
        ):
            raise FleetPlanInvalid("Claimed worker resources differ from original durable custody")
        resource_scope, resource_envelope = deferred.scope, worker_resource_envelope
    elif worker_resource_envelope is not None:
        raise FleetPlanInvalid("Unselected worker resource evidence cannot alter composition")
    journal = Journal(database)
    executor_configuration = restore_worker_executor_configuration(
        configuration.executor_configuration
    )
    fleet_provisioner = (
        restore_fleet_worker_authority(configuration.fleet_authority, executor_configuration)
        if configuration.fleet_authority is not None
        else None
    )
    path_authority = (
        executor_configuration.path_authority if executor_configuration is not None else None
    )
    extensions = FleetExtensionStore(configuration.extension_root, path_authority=path_authority)
    tools = FleetToolStore(configuration.tool_root, path_authority=path_authority)
    standard_isolation = (
        restore_worker_isolation_configuration(configuration.standard_process_isolation)
        if configuration.standard_process_isolation is not None
        else None
    )
    git = configuration.git.restore(isolation=standard_isolation)
    executor_pool, capabilities, content_store = _build_capability_runtime(
        database,
        journal,
        git,
        extensions,
        tools,
        configuration=executor_configuration,
        content_store_root=configuration.content_store_root,
        run_parent=configuration.run_parent,
        live_output=live_output,
        diagnostics=diagnostics,
        terminal=terminal,
        standard_process_isolation=standard_isolation,
    )
    lifecycle = FleetRunLifecycle(
        database,
        journal,
        execution,
        executor_pool,
        content_store,
        require_authenticated_fleet=configuration.require_authenticated_fleet,
        require_compiled_fleet_contract=configuration.require_compiled_fleet_contract,
        fleet_provisioner=fleet_provisioner,
        standard_local_fleet=configuration.standard_local_fleet,
        run_resource_scope=resource_scope,
        run_resource_envelope=resource_envelope,
    )
    attempts = RepositoryEffectAttemptStore(database)
    effects = RepositoryEffectExecution(
        capabilities,
        journal,
        attempts,
        git,
        effect_fence=RepositoryEffectFence(git),
        settlement=RepositoryEffectSettlement(database, journal, attempts),
        state_paths=state_paths,
    )
    return FleetRunExecution(lifecycle, FleetTargetScheduler(effects), live_output, diagnostics)
