from __future__ import annotations

import os
import sys
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import TYPE_CHECKING, Mapping, Protocol

from oh_goon.core.errors import ExecutorPlacementLost, FleetPlanInvalid
from oh_goon.core.models.agent_providers import ResolvedNativeAgent
from oh_goon.core.models.fleet_plan import ResolvedFleetPlan
from oh_goon.core.models.fleet_plan_evidence import PlanIsolationBackendEvidence
from oh_goon.core.models.fleet_plan_execution_policy import CicdExecutionPolicy
from oh_goon.core.models.fleet_plan_isolation import (
    CicdResourcePolicy,
    IsolationBackendCapabilities,
    isolation_requirements,
)
from oh_goon.core.models.lace_trusted_local import TrustedLocalRunConsent, TrustedLocalRunScope
from oh_goon.core.models.run import FleetRunDetail
from oh_goon.core.models.run_admission import ExplicitRepositoryScope, StrictFleetExecutionAdmission
from oh_goon.core.models.target import FleetPlanTarget
from oh_goon.core.models.termination import TerminationSignal
from oh_goon.core.ports.local_lace_intent import LocalLaceIntentReservation
from oh_goon.core.ports.original_mount_intent import OriginalSandboxMount
from oh_goon.core.vocabulary import FleetRunSource
from oh_goon.engine.command import (
    CommandOutputObserver,
    CommandProcessObserver,
    CommandResult,
    run_command,
)
from oh_goon.engine.command_supervision import CommandRuntimeSupervision
from oh_goon.engine.errors import OperationCancelledError
from oh_goon.integrations.fleet_plan_environment import direct_plan_environment
from oh_goon.integrations.fleet_plan_lace_policy import require_policy_aware_lace_backend
from oh_goon.integrations.fleet_plan_run import FleetPlanStepAttempt
from oh_goon.integrations.source_snapshot import SourceSnapshotBinding, verify_clean_source_snapshot

MAX_FLEET_STEP_CAPTURE_BYTES_PER_STREAM = 1024 * 1024

if TYPE_CHECKING:
    from oh_goon.engine.command import CommandTerminal


@dataclass(frozen=True, slots=True)
class StandardReadOnlyMount:
    """One explicitly selected native runtime, configuration or Skill input."""

    source: Path
    target: PurePosixPath

    def __post_init__(self) -> None:
        if not self.source.is_absolute() or not self.target.is_absolute():
            raise FleetPlanInvalid("Standard sandbox mounts require absolute paths")
        if ".." in self.target.parts or str(self.target) == "/":
            raise FleetPlanInvalid("Standard sandbox mount target is unsafe")
        object.__setattr__(self, "source", self.source.resolve(strict=True))


@dataclass(frozen=True, slots=True)
class StandardIsolatedProcessRequest:
    """Ordinary admitted host workload with concrete filesystem/IP restrictions.

    The caller retains the original workspace lease and supplies its verifier.
    This transports existing authority; it creates no strict custody or signer
    evidence and cannot be used to bypass the strict request's checks.
    """

    workspace: Path
    argv: tuple[str, ...]
    cwd: Path
    env: Mapping[str, str] = field(repr=False)
    backend_configuration_id: str
    require_workspace_current: Callable[[], None] = field(repr=False)
    read_only_mounts: tuple[StandardReadOnlyMount, ...] = ()
    broker_directory: Path | None = None
    private_configuration_directory: Path | None = None
    terminal: CommandTerminal | None = field(default=None, repr=False)
    input_data: bytes | None = field(default=None, repr=False)
    resources: CicdResourcePolicy = field(default_factory=CicdResourcePolicy)
    read_only_workspace: bool = False
    retain_output_tail: bool = False
    termination_requested: Callable[[], TerminationSignal | None] | None = field(
        default=None, repr=False
    )

    def __post_init__(self) -> None:
        if (
            not self.workspace.is_absolute()
            or not self.cwd.is_absolute()
            or not self.workspace.is_dir()
            or not self.cwd.is_dir()
            or not self.argv
            or any(type(token) is not str or not token or "\x00" in token for token in self.argv)
            or not callable(self.require_workspace_current)
            or not self.backend_configuration_id
            or type(self.read_only_workspace) is not bool
            or type(self.retain_output_tail) is not bool
        ):
            raise FleetPlanInvalid("Standard sandbox requires resolved workspace and launch intent")
        workspace = self.workspace.resolve(strict=True)
        cwd = self.cwd.resolve(strict=True)
        if not cwd.is_relative_to(workspace):
            raise FleetPlanInvalid("Standard sandbox cwd is outside the approved workspace")
        if self.terminal is not None and self.input_data is not None:
            raise FleetPlanInvalid("Standard sandbox terminal and input bytes are exclusive")
        if self.input_data is not None and (
            type(self.input_data) is not bytes or len(self.input_data) > 1024 * 1024
        ):
            raise FleetPlanInvalid("Standard sandbox input must be bounded bytes")
        if any(type(name) is not str or type(value) is not str for name, value in self.env.items()):
            raise FleetPlanInvalid("Standard sandbox environment requires resolved strings")
        object.__setattr__(self, "workspace", workspace)
        object.__setattr__(self, "cwd", cwd)
        object.__setattr__(self, "env", MappingProxyType(dict(self.env)))
        if self.private_configuration_directory is not None:
            directory = self.private_configuration_directory
            if not directory.is_absolute() or not directory.is_dir():
                raise FleetPlanInvalid(
                    "Native private configuration requires an exact public directory"
                )
            object.__setattr__(
                self, "private_configuration_directory", directory.resolve(strict=True)
            )


class ProcessIsolationBackend(Protocol):
    capabilities: IsolationBackendCapabilities

    def configuration_identity(self) -> str: ...

    def require_available(self, plan: ResolvedFleetPlan) -> None: ...

    def execute(
        self,
        request: IsolatedProcessRequest,
        *,
        timeout: float | None,
        cancel_requested: Callable[[], bool] | None,
        stream_output: CommandOutputObserver | None = None,
        process_observer: CommandProcessObserver | None = None,
        runtime_supervision: CommandRuntimeSupervision | None = None,
    ) -> CommandResult: ...


def native_profile_command_runner(
    repository: Path,
    native_agent: ResolvedNativeAgent,
    backend: ProcessIsolationBackend | None,
    workspace_pin: Callable[[Path], AbstractContextManager[Callable[[], None]]] | None,
    *,
    termination_requested: Callable[[], TerminationSignal | None] | None = None,
) -> Callable[..., CommandResult]:
    """Transport the frozen native profile to original Step and Git process births.

    Native Git retains its parser, configuration and hook semantics. Its hooks
    receive the same physical boundary as repository verification commands.
    """
    from oh_goon.integrations.fleet_plan_bubblewrap_isolation import BubblewrapProcessIsolation

    workspace = repository.resolve(strict=True)
    roots = tuple(Path(root) for root in native_agent.profile.workspace_roots)
    if (
        native_agent.agent not in native_agent.profile.allowed_agents
        or any(not root.is_absolute() or ".." in root.parts for root in roots)
        or (roots and not any(workspace.is_relative_to(root) for root in roots))
    ):
        raise FleetPlanInvalid("Native workload differs from its resolved agent/workspace policy")
    sandboxed = native_agent.profile.backend == "bubblewrap"
    standard_backend = backend if isinstance(backend, BubblewrapProcessIsolation) else None
    if sandboxed:
        if standard_backend is None or workspace_pin is None:
            raise FleetPlanInvalid(
                "Native sandbox execution requires backend and workspace authority"
            )
        if native_agent.backend_configuration_id != standard_backend.configuration_identity():
            raise FleetPlanInvalid("Native sandbox backend configuration changed after resolution")
        _require_standard_git_layout(workspace)
    elif any(value is not None for value in native_agent.profile.resources.model_dump().values()):
        raise FleetPlanInvalid("Native resource limits require the selected sandbox resource owner")

    common_mounts = native_runtime_read_only_mounts(workspace, native_agent) if sandboxed else ()

    def command_runner(
        *argv: str,
        timeout: float | None = 10.0,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        cancel_requested: Callable[[], bool] | None = None,
        stream_output: CommandOutputObserver | None = None,
        process_observer: CommandProcessObserver | None = None,
        runtime_supervision: CommandRuntimeSupervision | None = None,
        terminal: CommandTerminal | None = None,
        input_data: bytes | None = None,
        read_only_mounts: tuple[StandardReadOnlyMount, ...] = (),
        broker_directory: Path | None = None,
        private_configuration_directory: Path | None = None,
        read_only_workspace: bool = False,
        preserve_native_executable_path: bool = False,
        retain_output_tail: bool = False,
    ) -> CommandResult:
        if type(retain_output_tail) is not bool:
            raise FleetPlanInvalid("Native output tail selection must be a boolean")
        if cancel_requested is not None and cancel_requested():
            raise OperationCancelledError("Native workload cancelled before process launch")
        if sandboxed:
            require_native_workspace_runtime_separation(
                workspace, read_only_workspace=read_only_workspace
            )
        if not sandboxed:
            if (
                read_only_workspace
                or broker_directory is not None
                or private_configuration_directory is not None
            ):
                raise FleetPlanInvalid("Requested physical native guarantees require Bubblewrap")
            return run_command(
                *argv,
                timeout=timeout,
                cwd=cwd,
                env=env,
                cancel_requested=cancel_requested,
                termination_requested=termination_requested,
                stream_output=stream_output,
                process_observer=process_observer,
                runtime_supervision=runtime_supervision,
                terminal=terminal,
                input_data=input_data,
                max_capture_bytes_per_stream=MAX_FLEET_STEP_CAPTURE_BYTES_PER_STREAM,
                retain_output_tail=retain_output_tail,
                preserve_native_executable_path=preserve_native_executable_path,
            )
        assert standard_backend is not None and workspace_pin is not None
        with workspace_pin(workspace) as require_current:
            require_current()
            _require_standard_git_layout(workspace)
            request = StandardIsolatedProcessRequest(
                workspace=workspace,
                argv=tuple(argv),
                cwd=cwd or workspace,
                env=env
                if env is not None
                else direct_plan_environment((), host_environment=os.environ),
                backend_configuration_id=native_agent.backend_configuration_id or "",
                require_workspace_current=require_current,
                read_only_mounts=_combine_native_mounts(common_mounts, read_only_mounts),
                broker_directory=broker_directory,
                private_configuration_directory=private_configuration_directory,
                terminal=terminal,
                input_data=input_data,
                resources=native_agent.profile.resources,
                read_only_workspace=read_only_workspace,
                retain_output_tail=retain_output_tail,
                termination_requested=termination_requested,
            )
            return standard_backend.execute(
                request,
                timeout=timeout,
                cancel_requested=cancel_requested,
                stream_output=stream_output,
                process_observer=process_observer,
                runtime_supervision=runtime_supervision,
            )

    return command_runner


def _require_standard_git_layout(workspace: Path) -> None:
    git = workspace / ".git"
    if git.is_symlink() or (git.exists() and not git.is_dir()) or (git / "commondir").exists():
        raise FleetPlanInvalid(
            "Native sandbox requires repository-local Git metadata; external linked-worktree "
            "metadata has no scoped workspace-authority mount"
        )


def _loaded_native_host_runtime_roots() -> tuple[Path, ...]:
    return tuple(
        root.resolve(strict=True)
        for root in (Path(sys.prefix), Path(sys.base_prefix), Path(__file__).resolve().parents[1])
    )


def require_native_workspace_runtime_separation(
    repository: Path, *, read_only_workspace: bool
) -> None:
    """Keep native writes outside the loaded host control plane and interpreter.

    A read-only process may inspect the runtime's own checkout. A writable bind
    would also change host import authority, even when another sandbox alias is
    read-only. Run writable self-hosting from an installed runtime outside the
    target repository instead.
    """
    if read_only_workspace:
        return
    workspace = repository.resolve(strict=True)
    if any(
        root.is_relative_to(workspace) or workspace.is_relative_to(root)
        for root in _loaded_native_host_runtime_roots()
    ):
        raise FleetPlanInvalid(
            "Writable native workspace overlaps the loaded host control-plane or Python runtime; "
            "use an installed runtime outside the target repository"
        )


def native_runtime_read_only_mounts(
    workspace: Path, native_agent: ResolvedNativeAgent
) -> tuple[StandardReadOnlyMount, ...]:
    # These are the loaded runtime's existing paths, not a second installation
    # resolver or a broad bind of its source checkout/host home.
    roots = list(_loaded_native_host_runtime_roots())
    roots.extend(Path(root) for root in native_agent.definition.runtime_roots)
    if native_agent.definition.executable is not None:
        roots.append(Path(native_agent.definition.executable))
    public = tuple(Path(root).resolve() for root in ("/usr", "/bin", "/sbin", "/lib", "/lib64"))
    selected: list[Path] = []
    for root in sorted(
        {root.resolve(strict=True) for root in roots}, key=lambda path: len(path.parts)
    ):
        if root.is_relative_to(workspace) or any(root.is_relative_to(base) for base in public):
            continue
        if not any(root.is_relative_to(parent) for parent in selected):
            selected.append(root)
    mounts = [StandardReadOnlyMount(root, PurePosixPath(root)) for root in selected]
    for prefix in (sys.prefix, sys.base_prefix):
        native_prefix = Path(prefix)
        canonical_prefix = native_prefix.resolve(strict=True)
        if native_prefix != canonical_prefix and canonical_prefix in selected:
            mounts.append(StandardReadOnlyMount(canonical_prefix, PurePosixPath(native_prefix)))
    base = Path(sys.base_prefix).resolve(strict=True)
    native_base_executable = getattr(sys, "_base_executable", None)
    if not isinstance(native_base_executable, str) or not native_base_executable:
        raise FleetPlanInvalid("Loaded Python runtime has no exact base interpreter path")
    native_executable = Path(native_base_executable)
    executable = native_executable.resolve(strict=True)
    if executable.is_relative_to(base):
        suffix = executable.relative_to(base).parts
        if suffix and native_executable.parts[-len(suffix) :] == suffix:
            native_prefix = native_executable.parents[len(suffix) - 1]
            if native_prefix != base and base in selected:
                mounts.append(StandardReadOnlyMount(base, PurePosixPath(native_prefix)))
    return tuple(dict.fromkeys(mounts))


def _combine_native_mounts(
    common: tuple[StandardReadOnlyMount, ...], extra: tuple[StandardReadOnlyMount, ...]
) -> tuple[StandardReadOnlyMount, ...]:
    # Exact canonical inputs may already be part of the loaded native runtime.
    # A caller-selected distinct alias remains explicit and is validated by the
    # physical backend, which rejects ambiguous or overlapping mappings.
    return tuple(dict.fromkeys((*common, *extra)))


class TrustedLocalEffectAuthority(Protocol):
    """Original Fleet/Enforcer per-effect authority port; no local issuer.

    The port is intentionally not implemented here. Without the owned Fleet
    authority, the local backend refuses before process creation.
    """

    def require_effect_authority(self, request: IsolatedProcessRequest) -> None: ...


@dataclass(frozen=True, slots=True)
class IsolatedProcessRequest:
    """One process launch. All admitted authority is owned by ``attempt``."""

    attempt: FleetPlanStepAttempt
    argv: tuple[str, ...]
    cwd: Path
    env: dict[str, str]
    read_only_workspace: SourceSnapshotBinding | None = None
    # Only the original journal may project this from the signed source stage.
    # Native execution remains refused until original physical LACE custody qualifies.
    agent_source_mount: OriginalSandboxMount | None = None
    termination_requested: Callable[[], TerminationSignal | None] | None = None
    fleet_run_id: str | None = None
    step_id: str | None = None
    command_ordinal: int = 1
    local_resource_intent: LocalLaceIntentReservation | None = None
    # WF-03B: the original worker must supply this *after* durable Run
    # admission. A serialized consent alone is never an effect authority.
    trusted_local_consent: TrustedLocalRunConsent | None = None
    trusted_local_scope: TrustedLocalRunScope | None = None
    trusted_local_effect_authority: TrustedLocalEffectAuthority | None = None


def isolation_backend_evidence(backend: ProcessIsolationBackend) -> PlanIsolationBackendEvidence:
    capabilities = backend.capabilities
    return PlanIsolationBackendEvidence(
        backend_id=capabilities.backend_id,
        configuration_id=backend.configuration_identity(),
        kernel_boundary=capabilities.kernel_boundary.value,
        remote_execution=capabilities.remote_execution,
        experimental=capabilities.experimental,
    )


def verify_selected_backend(
    backend: ProcessIsolationBackend,
    request: IsolatedProcessRequest,
) -> None:
    if isolation_backend_evidence(backend) != request.attempt.isolation_backend:
        raise ExecutorPlacementLost(
            "Fleet process-isolation backend configuration changed after RUN_ADMITTED"
        )
    if request.read_only_workspace is not None:
        capabilities = backend.capabilities
        if not capabilities.private_filesystem or not capabilities.read_only_workspace:
            raise FleetPlanInvalid(
                "Read-only workspace execution requires a backend that physically enforces "
                "a private read-only workspace"
            )
        verify_clean_source_snapshot(
            request.read_only_workspace,
            label="read-only execution workspace",
        )


def admitted_tl1_run_context(
    plan: ResolvedFleetPlan,
    admitted: PlanIsolationBackendEvidence,
    admitted_run: FleetRunDetail,
) -> tuple[TrustedLocalRunConsent | None, TrustedLocalRunScope | None]:
    """Project optional TL-1 scope solely from original admitted journal facts.

    Neither the caller's Plan nor this projection issues permission to execute:
    the original worker must separately provide effect-time LACE authority. A
    restart can obtain the same facts from the original RunDetail projection.
    """
    run = admitted_run.run
    target = run.target
    execution = admitted_run.execution
    if not isinstance(target, FleetPlanTarget) or not isinstance(
        execution, StrictFleetExecutionAdmission
    ):
        raise FleetPlanInvalid("TL-1 worker requires original admitted strict Plan authority")
    if target.plan != plan:
        raise FleetPlanInvalid("Worker Plan differs from immutable RUN_ADMITTED Plan")
    if execution.placement.isolation_backend != admitted:
        raise FleetPlanInvalid("Worker isolation backend differs from RUN_ADMITTED placement")
    consent = execution.trusted_local_consent
    if consent is None:
        if admitted.backend_id == "trusted-local":
            raise FleetPlanInvalid("TL-1 backend requires original RUN_ADMITTED consent")
        return None, None
    if (
        admitted.backend_id != "trusted-local"
        or run.source is not FleetRunSource.MANUAL
        or not isinstance(run.repository_scope, ExplicitRepositoryScope)
        or len(run.repository_scope.repository_ids) != 1
        or len(run.targets) != 1
        or admitted_run.scheduling is not None
    ):
        raise FleetPlanInvalid(
            "TL-1 worker requires interactive single-repository manual admission"
        )
    if not isinstance(plan.execution, CicdExecutionPolicy):
        raise FleetPlanInvalid("TL-1 worker requires trusted CI/CD execution")
    return consent, TrustedLocalRunScope(
        fleet_run_id=run.fleet_run_id,
        execution_digest=target.execution_digest,
        executor_id=execution.placement.executor_capacity.executor_id,
        executor_generation=execution.placement.executor_capacity.executor_generation,
        source=run.source.value.lower(),
        all_repositories=False,
        repository_count=1,
        unattended=False,
        execution=plan.execution,
    )


def select_bound_process_isolation_backend(
    plan: ResolvedFleetPlan,
    admitted: PlanIsolationBackendEvidence,
    backends: tuple[ProcessIsolationBackend, ...],
    *,
    admitted_run: FleetRunDetail | None = None,
) -> ProcessIsolationBackend:
    """Resolve exactly the backend/configuration admitted before RUN_ADMITTED."""

    execution = plan.execution
    if not isinstance(execution, CicdExecutionPolicy):
        raise FleetPlanInvalid(
            "Bound process-isolation selection is only valid for strict CI/CD plans"
        )
    requirements = isolation_requirements(execution.trust_repository_code, execution.resources)
    consent: TrustedLocalRunConsent | None = None
    scope: TrustedLocalRunScope | None = None
    if admitted_run is not None:
        consent, scope = admitted_tl1_run_context(plan, admitted, admitted_run)
    if admitted.backend_id == "trusted-local" and (consent is None or scope is None):
        raise FleetPlanInvalid(
            "TL-1 worker requires original RUN_ADMITTED consent before backend selection"
        )
    if consent is not None and admitted.backend_id != "trusted-local":
        raise FleetPlanInvalid("TL-1 consent cannot authorize a strict backend")
    for backend in backends:
        if isolation_backend_evidence(backend) != admitted:
            continue
        if consent is None:
            if not backend.capabilities.satisfies(requirements):
                raise FleetPlanInvalid(
                    "Admitted Fleet process-isolation backend no longer satisfies the plan requirements"
                )
            require_policy_aware_lace_backend(execution=execution, backend=backend.capabilities)
        else:
            # Only exact, original Run-authoritative consent can replace the
            # default strict requirement. This is SELECTION, not effect authority.
            if scope is None:
                raise FleetPlanInvalid("TL-1 consent requires its original admitted Run scope")
            require_policy_aware_lace_backend(
                execution=execution,
                backend=backend.capabilities,
                consent=consent,
                fleet_run_id=scope.fleet_run_id,
                execution_digest=scope.execution_digest,
                backend_configuration_id=backend.configuration_identity(),
                executor_id=scope.executor_id,
                executor_generation=scope.executor_generation,
                source=scope.source,
                all_repositories=scope.all_repositories,
                repository_count=scope.repository_count,
                unattended=scope.unattended,
            )
        backend.require_available(plan)
        return backend
    raise FleetPlanInvalid(
        "The exact process-isolation backend/configuration admitted before RUN_ADMITTED "
        "is no longer configured"
    )
