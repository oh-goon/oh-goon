from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import Field, computed_field, model_validator

from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.models.fleet_plan_evidence import PlanIsolationBackendEvidence
from oh_goon.core.product_identity import stable_id


@dataclass(frozen=True, slots=True)
class FleetExecutorIdentity:
    """One logical executor generation; shared by transient configuration/capacity owners."""

    executor_id: str
    generation: int

    def __post_init__(self) -> None:
        if not self.executor_id:
            raise ValueError("Fleet executor identity requires a non-empty executor id")
        if self.generation < 1:
            raise ValueError("Fleet executor generation must be at least 1")


@dataclass(frozen=True, slots=True)
class ExecutorCapacityEvidenceInput:
    fleet_run_id: str
    executor: FleetExecutorIdentity
    source: str
    backend_configuration_id: str
    capacity_cpu_millis: int
    capacity_memory_bytes: int
    capacity_max_processes: int | None
    requested_parallelism: int
    admitted_parallelism: int
    per_attempt_cpu_millis: int | None
    per_attempt_memory_bytes: int | None
    per_attempt_max_processes: int | None
    pool_id: str
    placement_policy: str
    placement_rank: int
    executor_priority: int


class PlanExecutorCapacityEvidence(ImmutableModel):
    """Durable proof of aggregate executor budget reserved atomically with RUN_ADMITTED."""

    fleet_run_id: str = Field(min_length=1)
    pool_id: str = Field(min_length=1)
    placement_policy: Literal["priority_rendezvous_fifo_v2"]
    placement_rank: int = Field(ge=0, le=255)
    executor_priority: int = Field(ge=0, le=1_000_000)
    executor_id: str = Field(min_length=1, max_length=120)
    executor_generation: int = Field(ge=1)
    source: Literal["local_verified", "remote_operator_attested"]
    backend_configuration_id: str = Field(min_length=1)
    capacity_cpu_millis: int = Field(ge=100)
    capacity_memory_bytes: int = Field(ge=64 * 1024 * 1024)
    capacity_max_processes: int | None = Field(default=None, ge=16)
    requested_parallelism: int = Field(ge=1, le=32)
    admitted_parallelism: int = Field(ge=1, le=32)
    per_attempt_cpu_millis: int | None = Field(default=None, ge=100)
    per_attempt_memory_bytes: int | None = Field(default=None, ge=64 * 1024 * 1024)
    per_attempt_max_processes: int | None = Field(default=None, ge=16)

    @computed_field
    @property
    def inventory_id(self) -> str:
        return stable_id(
            "fleet-executor-capacity",
            _inventory_payload(
                self.executor_id,
                self.executor_generation,
                self.source,
                self.capacity_cpu_millis,
                self.capacity_memory_bytes,
                self.capacity_max_processes,
            ),
        )

    @computed_field
    @property
    def health_attestation_id(self) -> str:
        return stable_id(
            "fleet-executor-health-attestation",
            {
                "executor_id": self.executor_id,
                "executor_generation": self.executor_generation,
                "backend_configuration_id": self.backend_configuration_id,
                "inventory_id": self.inventory_id,
                "status": "available",
            },
        )

    @computed_field
    @property
    def placement_id(self) -> str:
        return stable_id(
            "fleet-executor-placement",
            _placement_payload(
                self.fleet_run_id,
                self.pool_id,
                self.executor_id,
                self.backend_configuration_id,
                self.inventory_id,
                self.placement_policy,
                self.placement_rank,
                self.executor_priority,
            ),
        )

    @computed_field
    @property
    def reservation_id(self) -> str:
        return stable_id(
            "fleet-capacity-reservation",
            _reservation_payload(
                self.fleet_run_id,
                self.placement_id,
                self.inventory_id,
                self.backend_configuration_id,
                self.requested_parallelism,
                self.admitted_parallelism,
                self.per_attempt_cpu_millis,
                self.per_attempt_memory_bytes,
                self.per_attempt_max_processes,
            ),
        )

    @computed_field
    @property
    def reserved_cpu_millis(self) -> int:
        if self.per_attempt_cpu_millis is None:
            return 0
        return self.per_attempt_cpu_millis * self.admitted_parallelism

    @computed_field
    @property
    def reserved_memory_bytes(self) -> int:
        if self.per_attempt_memory_bytes is None:
            return 0
        return self.per_attempt_memory_bytes * self.admitted_parallelism

    @computed_field
    @property
    def reserved_max_processes(self) -> int | None:
        if self.per_attempt_max_processes is None:
            return None
        return self.per_attempt_max_processes * self.admitted_parallelism

    @classmethod
    def create(cls, facts: ExecutorCapacityEvidenceInput) -> PlanExecutorCapacityEvidence:
        return cls(
            fleet_run_id=facts.fleet_run_id,
            pool_id=facts.pool_id,
            placement_policy=facts.placement_policy,
            placement_rank=facts.placement_rank,
            executor_priority=facts.executor_priority,
            executor_id=facts.executor.executor_id,
            executor_generation=facts.executor.generation,
            source=facts.source,
            backend_configuration_id=facts.backend_configuration_id,
            capacity_cpu_millis=facts.capacity_cpu_millis,
            capacity_memory_bytes=facts.capacity_memory_bytes,
            capacity_max_processes=facts.capacity_max_processes,
            requested_parallelism=facts.requested_parallelism,
            admitted_parallelism=facts.admitted_parallelism,
            per_attempt_cpu_millis=facts.per_attempt_cpu_millis,
            per_attempt_memory_bytes=facts.per_attempt_memory_bytes,
            per_attempt_max_processes=facts.per_attempt_max_processes,
        )

    @model_validator(mode="after")
    def validate_capacity(self) -> PlanExecutorCapacityEvidence:
        self._validate_limits()
        return self

    def _validate_limits(self) -> None:
        if self.admitted_parallelism > self.requested_parallelism:
            raise ValueError("Fleet admitted parallelism cannot exceed the plan maximum")
        if self.reserved_cpu_millis > self.capacity_cpu_millis:
            raise ValueError("Fleet capacity reservation exceeds executor CPU inventory")
        if self.reserved_memory_bytes > self.capacity_memory_bytes:
            raise ValueError("Fleet capacity reservation exceeds executor memory inventory")
        if self.reserved_max_processes is not None:
            if (
                self.capacity_max_processes is None
                or self.reserved_max_processes > self.capacity_max_processes
            ):
                raise ValueError("Fleet capacity reservation exceeds executor PID inventory")


def _inventory_payload(
    executor_id: str,
    executor_generation: int,
    source: str,
    cpu_millis: int,
    memory_bytes: int,
    max_processes: int | None,
) -> dict[str, object]:
    return {
        "executor_id": executor_id,
        "executor_generation": executor_generation,
        "source": source,
        "cpu_millis": cpu_millis,
        "memory_bytes": memory_bytes,
        "max_processes": max_processes,
    }


def _placement_payload(
    fleet_run_id: str,
    pool_id: str,
    executor_id: str,
    backend_configuration_id: str,
    inventory_id: str,
    placement_policy: str,
    placement_rank: int,
    executor_priority: int,
) -> dict[str, object]:
    return {
        "fleet_run_id": fleet_run_id,
        "pool_id": pool_id,
        "executor_id": executor_id,
        "backend_configuration_id": backend_configuration_id,
        "inventory_id": inventory_id,
        "placement_policy": placement_policy,
        "placement_rank": placement_rank,
        "executor_priority": executor_priority,
    }


def _reservation_payload(
    fleet_run_id: str,
    placement_id: str,
    inventory_id: str,
    backend_configuration_id: str,
    requested_parallelism: int,
    admitted_parallelism: int,
    per_attempt_cpu_millis: int | None,
    per_attempt_memory_bytes: int | None,
    per_attempt_max_processes: int | None,
) -> dict[str, object]:
    return {
        "fleet_run_id": fleet_run_id,
        "placement_id": placement_id,
        "inventory_id": inventory_id,
        "backend_configuration_id": backend_configuration_id,
        "requested_parallelism": requested_parallelism,
        "admitted_parallelism": admitted_parallelism,
        "per_attempt_cpu_millis": per_attempt_cpu_millis,
        "per_attempt_memory_bytes": per_attempt_memory_bytes,
        "per_attempt_max_processes": per_attempt_max_processes,
    }


class FleetExecutorPlacement(ImmutableModel):
    """Exact backend, capacity, and trust epoch committed together with RUN_ADMITTED."""

    isolation_backend: PlanIsolationBackendEvidence
    executor_capacity: PlanExecutorCapacityEvidence
    executor_trust_epoch: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_binding(self) -> FleetExecutorPlacement:
        if (
            self.executor_capacity.backend_configuration_id
            != self.isolation_backend.configuration_id
        ):
            raise ValueError(
                "Fleet executor-capacity reservation must match the admitted isolation backend"
            )
        return self
