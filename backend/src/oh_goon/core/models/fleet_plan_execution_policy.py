from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field

from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.models.fleet_plan_isolation import (
    CicdResourcePolicy,
    IsolationRequirements,
    IsolationThreatModel,
    isolation_requirements,
)


class ExecutionPurpose(StrEnum):
    CICD = "cicd"
    MAINTENANCE = "maintenance"
    MIGRATION = "migration"
    VERIFICATION = "verification"
    INSPECTION = "inspection"


class RepositoryExecutionMode(StrEnum):
    SERIAL = "serial"
    PARALLEL = "parallel"


class SerialRepositoryExecution(ImmutableModel):
    mode: Literal[RepositoryExecutionMode.SERIAL] = RepositoryExecutionMode.SERIAL


class ParallelRepositoryExecution(ImmutableModel):
    mode: Literal[RepositoryExecutionMode.PARALLEL] = RepositoryExecutionMode.PARALLEL
    max_parallel: int = Field(ge=2, le=32)


RepositoryExecutionPolicy = Annotated[
    SerialRepositoryExecution | ParallelRepositoryExecution,
    Field(discriminator="mode"),
]


class CicdExecutionPolicy(ImmutableModel):
    """Small authoring surface for strict CI/CD execution.

    Repository admission belongs exclusively to ``requirements``. Resource values are
    optional execution caps: omitted values inherit the selected executor's currently
    available capacity and are recorded as runtime capacity evidence instead of being
    replaced by hidden static defaults.
    """

    purpose: Literal[ExecutionPurpose.CICD] = ExecutionPurpose.CICD
    trust_repository_code: bool = True
    resources: CicdResourcePolicy = Field(default_factory=CicdResourcePolicy)
    repositories: RepositoryExecutionPolicy = Field(default_factory=SerialRepositoryExecution)


class MaintenanceExecutionPolicy(ImmutableModel):
    purpose: Literal[ExecutionPurpose.MAINTENANCE] = ExecutionPurpose.MAINTENANCE
    repositories: SerialRepositoryExecution = Field(default_factory=SerialRepositoryExecution)


class MigrationExecutionPolicy(ImmutableModel):
    purpose: Literal[ExecutionPurpose.MIGRATION] = ExecutionPurpose.MIGRATION
    repositories: SerialRepositoryExecution = Field(default_factory=SerialRepositoryExecution)


class VerificationExecutionPolicy(ImmutableModel):
    purpose: Literal[ExecutionPurpose.VERIFICATION] = ExecutionPurpose.VERIFICATION
    repositories: RepositoryExecutionPolicy = Field(default_factory=SerialRepositoryExecution)


class InspectionExecutionPolicy(ImmutableModel):
    purpose: Literal[ExecutionPurpose.INSPECTION] = ExecutionPurpose.INSPECTION
    repositories: RepositoryExecutionPolicy = Field(default_factory=SerialRepositoryExecution)


PlanExecutionPolicy = Annotated[
    CicdExecutionPolicy
    | MaintenanceExecutionPolicy
    | MigrationExecutionPolicy
    | VerificationExecutionPolicy
    | InspectionExecutionPolicy,
    Field(discriminator="purpose"),
]


class LaceRequiredGuarantees(ImmutableModel):
    """LACE-WF-01 demand only, compiled from one original execution policy.

    This projection does not select a backend, approve trusted-local execution,
    issue native custody, or supersede the original Enforcer admission policy.
    An unspecified workload profile MUST NOT be interpreted as permission to
    execute without isolation. Regardless of workload, any native kernel effect
    must still pass its original independently provisioned LACE custody gate.
    """

    purpose: ExecutionPurpose
    threat_model: IsolationThreatModel | None
    isolation: IsolationRequirements | None
    profile_status: Literal["defined", "not_defined"]
    native_kernel_effects: Literal["require_independent_custody"] = "require_independent_custody"
    trusted_local_execution: Literal["not_approved"] = "not_approved"

    def require_defined(self) -> IsolationRequirements:
        """Fail closed when a future consumer requires an explicit profile."""
        if self.profile_status != "defined" or self.isolation is None:
            raise ValueError(
                f"LACE guarantees not defined for execution purpose {self.purpose.value}; "
                "a policy review is required before using this profile"
            )
        return self.isolation


def compile_lace_required_guarantees(policy: PlanExecutionPolicy) -> LaceRequiredGuarantees:
    """Project policy-specific demands without adding a second isolation compiler.

    Strict CI/CD is already defined by the original backend requirements. The
    other purpose variants exist, but their required physical guarantees have
    not been reviewed for native/trusted-local execution: keep them explicit
    and unresolved for WF-02/WF-03 rather than silently treating them as safe.
    """
    if isinstance(policy, CicdExecutionPolicy):
        threat = (
            IsolationThreatModel.TRUSTED_REPOSITORY
            if policy.trust_repository_code
            else IsolationThreatModel.UNTRUSTED_REPOSITORY
        )
        return LaceRequiredGuarantees(
            purpose=policy.purpose,
            threat_model=threat,
            isolation=isolation_requirements(threat, policy.resources),
            profile_status="defined",
        )
    if isinstance(policy, (
        MaintenanceExecutionPolicy, MigrationExecutionPolicy,
        VerificationExecutionPolicy, InspectionExecutionPolicy,
    )):
        return LaceRequiredGuarantees(
            purpose=policy.purpose,
            threat_model=None,
            isolation=None,
            profile_status="not_defined",
        )
    raise ValueError("Unsupported canonical Plan execution policy")
