"""Passive observations of the native envelope containing one original CLI Run."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, computed_field, field_validator, model_validator

from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.models.fleet_plan_isolation import CicdResourcePolicy
from oh_goon.core.process_birth import ProcessBirthIdentity
from oh_goon.core.product_identity import stable_id


class RunConcurrencyPolicy(ImmutableModel):
    """Explicit two-worker ceilings under one operator-selected native slice."""

    max_active_runs: Literal[2] = 2
    aggregate_slice: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,248}\.slice$")
    aggregate_resources: CicdResourcePolicy
    worker_resources: CicdResourcePolicy
    controller_reserve: CicdResourcePolicy

    @model_validator(mode="after")
    def full_finite_partition(self) -> RunConcurrencyPolicy:
        for name in ("cpu_millis", "memory_mib", "max_processes"):
            aggregate = getattr(self.aggregate_resources, name)
            worker = getattr(self.worker_resources, name)
            reserve = getattr(self.controller_reserve, name)
            if any(type(value) is not int for value in (aggregate, worker, reserve)):
                raise ValueError("Two-Run resources require complete finite native ceilings")
            if self.max_active_runs * worker + reserve > aggregate:
                raise ValueError("Worker ceilings and controller reserve exceed aggregate budget")
        return self


class NativeRuntimeSliceEvidence(ImmutableModel):
    """Actual native aggregate observations, not a caller-issued resource grant."""

    kind: Literal["systemd_user_slice"] = "systemd_user_slice"
    unit_name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,248}\.slice$")
    invocation_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    controller_birth: ProcessBirthIdentity
    controller_cgroup_device: int = Field(ge=1)
    controller_cgroup_inode: int = Field(ge=1)
    cgroup_device: int = Field(ge=1)
    cgroup_inode: int = Field(ge=1)
    resources: CicdResourcePolicy
    cpu_max: str = Field(min_length=1, max_length=128)
    memory_max_bytes: int = Field(ge=64 * 1024 * 1024)
    memory_swap_max_bytes: Literal[0] = 0
    pids_max: int = Field(ge=16)
    manager_birth: ProcessBirthIdentity | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    manager_executable: str | None = Field(
        default=None, min_length=1, max_length=4096, exclude_if=lambda value: value is None
    )
    manager_executable_artifact_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$", exclude_if=lambda value: value is None
    )
    manager_version: str | None = Field(
        default=None, min_length=1, max_length=128, exclude_if=lambda value: value is None
    )
    manager_uid: int | None = Field(default=None, ge=0, exclude_if=lambda value: value is None)

    @model_validator(mode="after")
    def exact_native_limits(self) -> NativeRuntimeSliceEvidence:
        fields = self.cpu_max.split()
        if (
            self.invocation_id == "0" * 32
            or len(fields) != 2
            or any(
                not field.isascii() or not field.isdigit() or int(field) <= 0 for field in fields
            )
            or self.resources.cpu_millis is None
            or self.resources.memory_bytes is None
            or self.resources.max_processes is None
            or int(fields[0]) * 1000 != int(fields[1]) * self.resources.cpu_millis
            or self.memory_max_bytes != self.resources.memory_bytes
            or self.pids_max != self.resources.max_processes
        ):
            raise ValueError("Native aggregate controls differ from explicit resource ceilings")
        return self


class RunConcurrencyAdmission(ImmutableModel):
    """Shared native budget plus one original Run's distinct scope intent."""

    policy: RunConcurrencyPolicy
    aggregate: NativeRuntimeSliceEvidence
    fleet_run_id: str = Field(min_length=1, max_length=160)
    worker_unit_name: str = Field(pattern=r"^oh-goon-run-[A-Za-z0-9_.-]+\.scope$", max_length=255)
    worker_scope_identity: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")

    @model_validator(mode="after")
    def native_budget_matches(self) -> RunConcurrencyAdmission:
        if (
            self.policy.aggregate_slice != self.aggregate.unit_name
            or self.policy.aggregate_resources != self.aggregate.resources
        ):
            raise ValueError("Run concurrency budget differs from its native aggregate")
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def budget_identity(self) -> str:
        return stable_id(
            "run-concurrency-budget",
            {
                "policy": self.policy.model_dump(mode="json"),
                "aggregate": self.aggregate.model_dump(mode="json"),
            },
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def identity(self) -> str:
        return stable_id(
            "run-concurrency-admission", self.model_dump(mode="json", exclude_computed_fields=True)
        )


class RunResourceEnvelopeEvidence(ImmutableModel):
    """Native launch facts; constructing this model never grants execution authority."""

    kind: Literal["systemd_user_scope"] = "systemd_user_scope"
    subject: Literal["cli", "worker"] = Field(
        default="cli", exclude_if=lambda value: value == "cli"
    )
    fleet_run_id: str = Field(min_length=1, max_length=160)
    unit_name: str = Field(pattern=r"^oh-goon-run-[A-Za-z0-9_.-]+\.scope$", max_length=255)
    invocation_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    handoff_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    process_birth: ProcessBirthIdentity
    resources: CicdResourcePolicy
    cgroup_device: int = Field(ge=1)
    cgroup_inode: int = Field(ge=1)
    control_group: str | None = Field(
        default=None, min_length=1, max_length=4096, exclude_if=lambda value: value is None
    )
    cpu_max: str = Field(min_length=1, max_length=128)
    memory_max_bytes: int | None = Field(ge=0)
    memory_swap_max_bytes: int | None = Field(ge=0)
    pids_max: int | None = Field(ge=1)
    oom_policy: Literal["kill"] | None = None
    memory_oom_group: bool | None = None

    @field_validator("invocation_id")
    @classmethod
    def require_native_invocation(cls, value: str) -> str:
        if value == "0" * 32:
            raise ValueError("native invocation identity must be observed")
        return value

    @model_validator(mode="after")
    def validate_observed_limits(self) -> RunResourceEnvelopeEvidence:
        fields = self.cpu_max.split()
        if (
            len(fields) != 2
            or not fields[1].isascii()
            or not fields[1].isdigit()
            or int(fields[1]) <= 0
        ):
            raise ValueError("native CPU observation requires a quota and positive period")
        quota = fields[0]
        if quota != "max" and (not quota.isascii() or not quota.isdigit() or int(quota) <= 0):
            raise ValueError("native CPU observation requires a valid quota")
        if self.resources.cpu_millis is not None and (
            quota == "max" or int(quota) * 1000 > int(fields[1]) * self.resources.cpu_millis
        ):
            raise ValueError("native CPU observation does not establish the selected ceiling")
        if self.resources.memory_bytes is not None and (
            self.memory_max_bytes is None
            or self.memory_max_bytes > self.resources.memory_bytes
            or self.memory_swap_max_bytes != 0
            or self.oom_policy != "kill"
            or self.memory_oom_group is not True
        ):
            raise ValueError("native memory observations do not establish the selected ceiling")
        if self.resources.max_processes is not None and (
            self.pids_max is None or self.pids_max > self.resources.max_processes
        ):
            raise ValueError("native PID observation does not establish the selected ceiling")
        return self


class RunResourceEnvelopeObservation(ImmutableModel):
    """Final native observation and cleanup convergence, separate from Run result truth."""

    kind: Literal["systemd_user_scope"] = "systemd_user_scope"
    fleet_run_id: str = Field(min_length=1, max_length=160)
    unit_name: str = Field(pattern=r"^oh-goon-run-[A-Za-z0-9_.-]+\.scope$", max_length=255)
    invocation_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    state: Literal["TERMINAL", "INCOMPLETE"]
    active_state: str | None = Field(default=None, max_length=64)
    result: str | None = Field(default=None, max_length=64)
    cpu_usage_usec: int | None = Field(default=None, ge=0)
    memory_peak_bytes: int | None = Field(default=None, ge=0)
    pids_peak: int | None = Field(default=None, ge=0)
    oom_kill_count: int | None = Field(default=None, ge=0)
    cleanup: Literal["VERIFIED", "INCOMPLETE"]
    reason_code: str | None = Field(default=None, max_length=128, pattern=r"^[A-Z0-9_]+$")
    native_witness: Literal["original_kernel_scope", "original_systemd_journal"] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    native_witness_identity: str | None = Field(
        default=None, pattern=r"^sha256:[0-9a-f]{64}$", exclude_if=lambda value: value is None
    )

    @field_validator("invocation_id")
    @classmethod
    def require_native_invocation(cls, value: str | None) -> str | None:
        if value == "0" * 32:
            raise ValueError(
                "native invocation identity must be observed or explicitly unavailable"
            )
        return value
