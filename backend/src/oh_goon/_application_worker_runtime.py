"""Compose the existing Fleet execution owner from one resolved Run handoff.

This is the worker-side portion of application wiring. It owns neither ingress,
startup recovery nor scheduling, and is called only after SQLite custody claim.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, cast

from pydantic import ConfigDict, Field, JsonValue, model_validator

from oh_goon._application_foundation import _build_capability_runtime
from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.models.run_resources import RunResourceEnvelopeEvidence
from oh_goon.core.path_layouts import ApplicationStatePathLayout
from oh_goon.core.ports.diagnostics import DiagnosticEmitter
from oh_goon.engine.command import CommandTerminal
from oh_goon.engine.fleet_extensions import FleetExtensionStore
from oh_goon.engine.fleet_tools import FleetToolStore
from oh_goon.engine.git import GitService
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

    @classmethod
    def capture(cls, git: GitService | None) -> WorkerGitConfiguration:
        if (
            type(git) is not GitService
            or git.command_runner is not None
            or git.observation_runner is not None
        ):
            raise FleetPlanInvalid(
                "Run worker requires native Git configuration without injected callbacks"
            )
        return cls(
            executable=git.executable,
            default_timeout_seconds=git.default_timeout_seconds,
        )

    def restore(self) -> GitService:
        return GitService(
            executable=self.executable, default_timeout_seconds=self.default_timeout_seconds
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
        return self


def capture_runtime_worker_configuration(
    foundation: RuntimeFoundation, fleet_run_id: str
) -> RunWorkerConfiguration:
    """Capture the already resolved parent inputs, including only this Run's enrollment."""
    paths = ApplicationStatePathLayout.from_database(foundation.database.path)
    return RunWorkerConfiguration(
        fleet_run_id=fleet_run_id,
        hashmarks_implementation_identity=require_loaded_hashmarks_runtime(),
        extension_root=foundation.extension_store.root,
        tool_root=foundation.tool_store.root,
        content_store_root=foundation.content_store.root,
        run_parent=paths.run_workspaces,
        git=WorkerGitConfiguration.capture(foundation.git_service),
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
    )


def build_claimed_run_execution(
    database: LocalDatabase,
    claimed_ticket: ExecutionTicket,
    configuration: RunWorkerConfiguration,
    *,
    terminal: CommandTerminal | None,
    live_output: GameTapeLiveOutputStore,
    diagnostics: DiagnosticEmitter,
) -> FleetRunExecution:
    """Build only the existing execution graph after the canonical worker claim."""
    if claimed_ticket.require_fleet_run_id() != configuration.fleet_run_id:
        raise FleetPlanInvalid("Run worker configuration differs from claimed Run")
    execution = ExecutionCoordinator(database)
    execution.require_claimed_fleet_worker(claimed_ticket)
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
    git = configuration.git.restore()
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
        standard_process_isolation=(
            restore_worker_isolation_configuration(configuration.standard_process_isolation)
            if configuration.standard_process_isolation is not None
            else None
        ),
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
        run_resource_scope=(
            ResolvedRunResourceScope.from_payload(configuration.run_resource_scope)
            if configuration.run_resource_scope is not None
            else None
        ),
        run_resource_envelope=configuration.run_resource_envelope,
    )
    attempts = RepositoryEffectAttemptStore(database)
    effects = RepositoryEffectExecution(
        capabilities,
        journal,
        attempts,
        git,
        effect_fence=RepositoryEffectFence(git),
        settlement=RepositoryEffectSettlement(database, journal, attempts),
    )
    return FleetRunExecution(lifecycle, FleetTargetScheduler(effects), live_output, diagnostics)
