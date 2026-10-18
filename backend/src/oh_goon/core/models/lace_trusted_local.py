"""Explicit TL-1 operator consent, not an executor or a kernel custody token.

The admission/worker owners must transport and revalidate this exact frozen
record.  It never authorizes a backend by merely appearing in a Plan.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import Field, model_validator

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.models.fleet_plan_execution_policy import (
    CicdExecutionPolicy,
    PlanExecutionPolicy,
)


class TrustedLocalRunConsent(ImmutableModel):
    """Operator approval bound to one Run, one immutable Plan and one backend.

    This is deliberately *not* a Plan field, an environment toggle, a reusable
    signed token or evidence of kernel-enforced filesystem/network isolation.
    """

    policy: Literal["tl-1"] = "tl-1"
    fleet_run_id: str = Field(min_length=1)
    execution_digest: str = Field(pattern=r"^(?:sha256:)?[0-9a-f]{64}$")
    backend_id: str = Field(min_length=1)
    backend_configuration_id: str = Field(min_length=1)
    executor_id: str = Field(min_length=1)
    executor_generation: int = Field(ge=1)
    operator_interactive: Literal[True]
    explicitly_acknowledged_ambient_host_access: Literal[True]
    source: Literal["manual"] = "manual"
    filesystem_access: Literal["operator_host_permissions"] = "operator_host_permissions"
    network_access: Literal["operator_host_permissions"] = "operator_host_permissions"
    process_access: Literal["operator_host_permissions"] = "operator_host_permissions"

    @model_validator(mode="after")
    def require_tl1_policy(self) -> TrustedLocalRunConsent:
        if self.backend_id in {"bubblewrap", "opensandbox"}:
            raise ValueError(
                "TL-1 cannot relabel a strict isolation backend as trusted-local"
            )
        return self

    def require_exact_authority(
        self,
        *,
        fleet_run_id: str,
        execution_digest: str,
        backend_id: str,
        backend_configuration_id: str,
        executor_id: str,
        executor_generation: int,
        execution: PlanExecutionPolicy,
        source: str,
        all_repositories: bool,
        repository_count: int,
        unattended: bool = False,
    ) -> None:
        """Validate the original admission facts without granting execution.

        A consumed Run must revalidate at original admission and worker entry;
        equality here is necessary, never sufficient, for native execution.
        """
        if (
            not isinstance(execution, CicdExecutionPolicy)
            or not execution.trust_repository_code
            or execution.repositories.mode != "serial"
            or execution.resources.cpu_millis is not None
            or execution.resources.memory_mib is not None
            or execution.resources.max_processes is not None
        ):
            raise FleetPlanInvalid("TL-1 requires trusted serial CI/CD without kernel resource-cap guarantees")
        if (
            unattended or source != "manual" or all_repositories
            or repository_count != 1
        ):
            raise FleetPlanInvalid("TL-1 requires one interactive operator-selected repository and manual Run")
        if (
            self.fleet_run_id != fleet_run_id
            or self.execution_digest != execution_digest
            or self.backend_id != backend_id
            or self.backend_configuration_id != backend_configuration_id
            or self.executor_id != executor_id
            or self.executor_generation != executor_generation
        ):
            raise FleetPlanInvalid("TL-1 consent is stale or belongs to another Run, Plan or backend")


@dataclass(frozen=True, slots=True)
class TrustedLocalRunScope:
    """Original admitted Run/placement facts, not an authorization token.

    These values must eventually be transported by the original Fleet journal
    and supplied by its owned worker. They cannot be inferred from a Plan.
    """
    fleet_run_id: str
    execution_digest: str
    executor_id: str
    executor_generation: int
    source: str
    all_repositories: bool
    repository_count: int
    unattended: bool
    execution: CicdExecutionPolicy

