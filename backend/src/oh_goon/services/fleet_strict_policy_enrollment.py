"""M176: deployment enrollment for strict CI/CD before placement exists.

The deployment freezes the requested Run/Plan/repository authority. Only the
original scheduler can later supply a strict placement and scheduling receipt;
that fresh authority is never guessed or preassembled by the enrollment.
"""

from __future__ import annotations

from dataclasses import dataclass

from oh_goon.core.enforcer_lace_trust import TrustProtocolError
from oh_goon.core.models.capability import CapabilitySnapshot
from oh_goon.core.models.fleet_plan_execution_policy import CicdExecutionPolicy
from oh_goon.core.models.run_admission import (
    FleetRepositoryScope,
    FleetRunAdmissionIntent,
    PlanRepositoryAdmissionEvidence,
    StrictFleetExecutionAdmission,
)
from oh_goon.core.models.run_launch import ApplicationLaunchReceipt
from oh_goon.core.models.scheduling import SchedulingPriority
from oh_goon.core.models.target import FleetPlanTarget
from oh_goon.core.vocabulary import FleetRunSource, OperationAction
from oh_goon.services.fleet_policy_enrollment import FleetPolicyEnrollment


@dataclass(frozen=True, slots=True)
class StrictFleetEnrollmentIntent:
    """Only pre-placement authority; no fake executor reservation or receipt."""

    source: FleetRunSource
    target: FleetPlanTarget
    capability: CapabilitySnapshot
    repository_admissions: tuple[PlanRepositoryAdmissionEvidence, ...]
    application_launch: ApplicationLaunchReceipt
    requested_priority: SchedulingPriority = SchedulingPriority.NORMAL

    def __post_init__(self) -> None:
        if (
            not isinstance(self.target, FleetPlanTarget)
            or not isinstance(self.target.plan.execution, CicdExecutionPolicy)
            or not self.repository_admissions
            or any(item.plan_id != self.target.plan.plan_id for item in self.repository_admissions)
            or not self.capability.matches_intent(OperationAction.EXECUTE_PLAN, self.target)
            or not isinstance(self.source, FleetRunSource)
            or not isinstance(self.requested_priority, SchedulingPriority)
        ):
            raise TrustProtocolError("exact strict CI/CD pre-placement Run intent required")


@dataclass(frozen=True, slots=True)
class StrictFleetPolicyEnrollment(FleetPolicyEnrollment):
    """One policy enrollment whose placement can be supplied only by FleetExecutorPool."""

    intent: StrictFleetEnrollmentIntent

    def __post_init__(self) -> None:
        super(StrictFleetPolicyEnrollment, self).__post_init__()
        if type(self.intent) is not StrictFleetEnrollmentIntent:
            raise TrustProtocolError("strict CI/CD enrollment requires pre-placement intent")

    def require_preplanning(self, *, run_id: str, target: FleetPlanTarget) -> None:
        if (
            type(self) is not StrictFleetPolicyEnrollment
            or self.run_id != run_id
            or target != self.intent.target
        ):
            raise TrustProtocolError("strict Fleet enrollment differs from requested Run or Plan")
        self.deployment_policy.read()
        self.configured.require_unchanged(self.current)

    def require_matching(
        self,
        *,
        run_id: str,
        intent: FleetRunAdmissionIntent,
        scope: FleetRepositoryScope,
        targets: list,
    ) -> None:
        self.require_preplanning(run_id=run_id, target=intent.target)
        execution = intent.execution
        scheduling = intent.scheduling
        expected = self.intent
        if (
            not isinstance(execution, StrictFleetExecutionAdmission)
            or execution.readmitted_from_run_id is not None
            or execution.rerun_from_run_id is not None
            or intent.source != expected.source
            or intent.capability != expected.capability
            or intent.repository_admissions != expected.repository_admissions
            or intent.application_launch != expected.application_launch
            or scope != self.repository_scope
            or tuple(targets) != self.targets
            or scheduling is None
            or scheduling.run_id != run_id
            or scheduling.requested_priority != expected.requested_priority
            or execution.placement.executor_capacity.fleet_run_id != run_id
            or execution.placement.executor_capacity.executor_id != self.configured.executor_id
            or execution.placement.executor_capacity.executor_generation
            != self.configured.executor_generation
            or execution.placement.isolation_backend.backend_id != self.configured.backend
            or execution.placement.isolation_backend.configuration_id
            != self.configured.backend_configuration_identity
        ):
            raise TrustProtocolError(
                "strict Fleet enrollment differs from original placed Plan authority"
            )
