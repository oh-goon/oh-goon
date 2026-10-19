"""WF-03B: truthful TL-1 local backend implementing the existing process port.

Not registered in the Fleet executor inventory. This is a backend implementation,
not a Run enrollment mechanism: it cannot execute absent a worker-supplied,
original-authority effect fence and exact per-Run operator consent.
"""

from __future__ import annotations

import os
from collections.abc import Callable

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.fleet_plan import ResolvedFleetPlan
from oh_goon.core.models.fleet_plan_execution_policy import CicdExecutionPolicy
from oh_goon.core.models.fleet_plan_isolation import (
    IsolationBackendCapabilities,
    IsolationKernelBoundary,
)
from oh_goon.core.product_identity import stable_id
from oh_goon.engine.command import (
    CommandOutputObserver,
    CommandProcessObserver,
    CommandResult,
    run_command,
)
from oh_goon.engine.command_supervision import CommandRuntimeSupervision
from oh_goon.integrations.fleet_plan_lace_policy import require_policy_aware_lace_backend
from oh_goon.integrations.fleet_plan_process_isolation import (
    MAX_FLEET_STEP_CAPTURE_BYTES_PER_STREAM,
    IsolatedProcessRequest,
    verify_selected_backend,
)


class TrustedLocalProcessIsolation:
    """A locally supervised *host process*, never a kernel sandbox.

    No cgroup, network namespace or filesystem isolation is claimed. The
    deployment still does not recognize TL-1 in its executor inventory; WF-03C
    must persist admission/consent and connect original worker authority first.
    """

    capabilities = IsolationBackendCapabilities(
        backend_id="trusted-local",
        private_filesystem=False,
        read_only_workspace=False,
        private_process_tree=False,
        enforced_network_policy=False,
        content_identified_runtime=False,
        exact_argv=True,
        disposable_attempt=False,
        immutable_authority_after_start=False,
        enforced_cpu_limit=False,
        enforced_memory_limit=False,
        enforced_process_limit=False,
        kernel_boundary=IsolationKernelBoundary.SHARED_HOST_KERNEL,
        remote_execution=False,
        experimental=True,
    )

    def configuration_identity(self) -> str:
        return stable_id(
            "process-isolation-backend",
            {
                "backend": self.capabilities.backend_id,
                "policy": "tl-1",
                "filesystem": "operator_host_permissions",
                "network": "operator_host_permissions",
                "process": "operator_host_permissions",
            },
        )

    def require_available(self, plan: ResolvedFleetPlan) -> None:
        if os.name == "nt":
            raise FleetPlanInvalid("TL-1 local backend requires Linux/WSL process supervision")
        execution = plan.execution
        if not isinstance(execution, CicdExecutionPolicy) or not execution.trust_repository_code:
            raise FleetPlanInvalid("TL-1 local backend accepts only trusted CI/CD Plans")
        if (
            execution.resources.cpu_millis is not None
            or execution.resources.memory_mib is not None
            or execution.resources.max_processes is not None
        ):
            raise FleetPlanInvalid("TL-1 cannot enforce declared kernel resource limits")
        if any(step.capabilities.egress.mode != "host_unrestricted" for step in plan.steps):
            raise FleetPlanInvalid(
                "TL-1 requires each Step to explicitly declare host_unrestricted egress; "
                "a denied or filtered network policy cannot be enforced by host permissions"
            )

    def execute(
        self,
        request: IsolatedProcessRequest,
        *,
        timeout: float | None,
        cancel_requested: Callable[[], bool] | None,
        stream_output: CommandOutputObserver | None = None,
        process_observer: CommandProcessObserver | None = None,
        runtime_supervision: CommandRuntimeSupervision | None = None,
    ) -> CommandResult:
        # No effect, not even subprocess creation, before exact original
        # admission/custody and per-Run policy checks. A caller-supplied
        # consent document is never itself an execution permit.
        verify_selected_backend(self, request)
        consent = request.trusted_local_consent
        scope = request.trusted_local_scope
        authority = request.trusted_local_effect_authority
        if consent is None or scope is None or authority is None:
            raise FleetPlanInvalid(
                "TL-1 requires enrolled Run consent and original effect authority"
            )
        if request.fleet_run_id != scope.fleet_run_id:
            raise FleetPlanInvalid("TL-1 physical Run identity differs from admitted scope")
        if request.attempt.nested_strict_execution or request.local_resource_intent is not None:
            raise FleetPlanInvalid("TL-1 cannot impersonate original strict LACE resources")
        if request.attempt.egress.mode != "host_unrestricted":
            raise FleetPlanInvalid("TL-1 cannot enforce a denied or filtered Step network policy")
        if (
            request.attempt.resources.cpu_millis is not None
            or request.attempt.resources.memory_mib is not None
            or request.attempt.resources.max_processes is not None
        ):
            raise FleetPlanInvalid("TL-1 cannot enforce kernel resource limits")
        if not request.argv or not request.cwd.is_dir():
            raise FleetPlanInvalid("TL-1 requires an exact command and existing working directory")
        require_policy_aware_lace_backend(
            execution=scope.execution,
            backend=self.capabilities,
            consent=consent,
            fleet_run_id=scope.fleet_run_id,
            execution_digest=scope.execution_digest,
            backend_configuration_id=self.configuration_identity(),
            executor_id=scope.executor_id,
            executor_generation=scope.executor_generation,
            source=scope.source,
            all_repositories=scope.all_repositories,
            repository_count=scope.repository_count,
            unattended=scope.unattended,
        )
        # The only effect permission must come from the original admitted Fleet
        # worker. WF-03B does not invent a new issuer or accept a boolean permit.
        authority.require_effect_authority(request)
        return run_command(
            *request.argv,
            cwd=request.cwd,
            env=request.env,
            timeout=None if runtime_supervision is not None else timeout,
            cancel_requested=cancel_requested,
            termination_requested=request.termination_requested,
            stream_output=stream_output,
            process_observer=process_observer,
            runtime_supervision=runtime_supervision,
            require_process_group_quiescence=True,
            parent_death_signal=True,
            max_capture_bytes_per_stream=MAX_FLEET_STEP_CAPTURE_BYTES_PER_STREAM,
        )
