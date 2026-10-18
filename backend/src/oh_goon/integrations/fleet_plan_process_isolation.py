from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from oh_goon.core.ports.local_lace_intent import LocalLaceIntentReservation
from oh_goon.core.ports.original_mount_intent import OriginalSandboxMount
from oh_goon.core.errors import ExecutorPlacementLost, FleetPlanInvalid
from oh_goon.core.models.fleet_plan import ResolvedFleetPlan
from oh_goon.core.models.fleet_plan_evidence import PlanIsolationBackendEvidence
from oh_goon.core.models.fleet_plan_execution_policy import CicdExecutionPolicy
from oh_goon.core.models.run import FleetRunDetail
from oh_goon.core.models.run_admission import ExplicitRepositoryScope, StrictFleetExecutionAdmission
from oh_goon.core.models.target import FleetPlanTarget
from oh_goon.core.vocabulary import FleetRunSource
from oh_goon.core.models.lace_trusted_local import TrustedLocalRunConsent, TrustedLocalRunScope
from oh_goon.core.models.fleet_plan_isolation import (
    IsolationBackendCapabilities,
    isolation_requirements,
)
from oh_goon.engine.command import CommandOutputObserver, CommandProcessObserver, CommandResult
from oh_goon.engine.command_supervision import CommandRuntimeSupervision
from oh_goon.core.models.termination import TerminationSignal
from oh_goon.integrations.fleet_plan_run import FleetPlanStepAttempt
from oh_goon.integrations.source_snapshot import SourceSnapshotBinding, verify_clean_source_snapshot
from oh_goon.integrations.fleet_plan_lace_policy import require_policy_aware_lace_backend


MAX_FLEET_STEP_CAPTURE_BYTES_PER_STREAM = 1024 * 1024


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
    if not isinstance(target, FleetPlanTarget) or not isinstance(execution, StrictFleetExecutionAdmission):
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
    if (admitted.backend_id != "trusted-local"
            or run.source is not FleetRunSource.MANUAL
            or not isinstance(run.repository_scope, ExplicitRepositoryScope)
            or len(run.repository_scope.repository_ids) != 1
            or len(run.targets) != 1
            or admitted_run.scheduling is not None):
        raise FleetPlanInvalid("TL-1 worker requires interactive single-repository manual admission")
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
        raise FleetPlanInvalid("TL-1 worker requires original RUN_ADMITTED consent before backend selection")
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
            require_policy_aware_lace_backend(
                execution=execution, backend=backend.capabilities, consent=consent,
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
