"""Original process-backend policy checks: strict remains the default.

TL-1 is eligible only when an exact human consent was transported by the
original Run authority.  This module does not create a process launcher or
native custody token; a backend remains responsible for its actual effects.
"""

from __future__ import annotations

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.fleet_plan_execution_policy import (
    CicdExecutionPolicy,
    LaceRequiredGuarantees,
    compile_lace_required_guarantees,
)
from oh_goon.core.models.fleet_plan_isolation import (
    IsolationBackendCapabilities,
    IsolationRequirements,
)
from oh_goon.core.models.lace_trusted_local import TrustedLocalRunConsent


def require_policy_aware_lace_backend(
    *,
    execution: CicdExecutionPolicy,
    backend: IsolationBackendCapabilities,
    consent: TrustedLocalRunConsent | None = None,
    fleet_run_id: str | None = None,
    execution_digest: str | None = None,
    backend_configuration_id: str | None = None,
    executor_id: str | None = None,
    executor_generation: int | None = None,
    source: str | None = None,
    all_repositories: bool | None = None,
    repository_count: int | None = None,
    unattended: bool | None = None,
) -> IsolationRequirements | None:
    """Check original strict demand, or only *eligibility* for narrow TL-1.

    None on TL-1 means ambient host access, NOT satisfied sandbox requirements;
    it must never be passed as a successful strict capability comparison.
    Backend launch still requires its separate original admission/custody path.
    """
    guarantees: LaceRequiredGuarantees = compile_lace_required_guarantees(execution)
    strict_demand = guarantees.require_defined()
    if consent is None:
        if not backend.satisfies(strict_demand):
            raise FleetPlanInvalid("Selected backend lacks required strict LACE guarantees")
        return strict_demand
    if (
        fleet_run_id is None
        or execution_digest is None
        or backend_configuration_id is None
        or executor_id is None
        or executor_generation is None
        or source is None
        or type(all_repositories) is not bool
        or type(repository_count) is not int
        or type(unattended) is not bool
    ):
        raise FleetPlanInvalid("TL-1 requires the exact original Run and selected backend identity")
    consent.require_exact_authority(
        fleet_run_id=fleet_run_id,
        execution_digest=execution_digest,
        backend_id=backend.backend_id,
        backend_configuration_id=backend_configuration_id,
        executor_id=executor_id,
        executor_generation=executor_generation,
        execution=execution,
        source=source,
        all_repositories=all_repositories,
        repository_count=repository_count,
        unattended=unattended,
    )
    if (
        backend.remote_execution
        or backend.backend_id in {"bubblewrap", "opensandbox"}
        or backend.private_filesystem
        or backend.private_process_tree
        or backend.enforced_network_policy
        or backend.enforced_cpu_limit
        or backend.enforced_memory_limit
        or backend.enforced_process_limit
        or not backend.exact_argv
    ):
        raise FleetPlanInvalid(
            "TL-1 requires a separately configured local backend reporting actual host permissions"
        )
    return None
