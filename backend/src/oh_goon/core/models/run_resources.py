"""Passive observations of the native envelope containing one original CLI Run."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator, model_validator

from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.models.fleet_plan_isolation import CicdResourcePolicy
from oh_goon.core.process_birth import ProcessBirthIdentity


class RunResourceEnvelopeEvidence(ImmutableModel):
    """Native launch facts; constructing this model never grants execution authority."""

    kind: Literal["systemd_user_scope"] = "systemd_user_scope"
    fleet_run_id: str = Field(min_length=1, max_length=160)
    unit_name: str = Field(pattern=r"^oh-goon-run-[A-Za-z0-9_.-]+\.scope$", max_length=255)
    invocation_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    handoff_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    process_birth: ProcessBirthIdentity
    resources: CicdResourcePolicy
    cgroup_device: int = Field(ge=1)
    cgroup_inode: int = Field(ge=1)
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
        if len(fields) != 2 or not fields[1].isascii() or not fields[1].isdigit() or int(fields[1]) <= 0:
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

    @field_validator("invocation_id")
    @classmethod
    def require_native_invocation(cls, value: str | None) -> str | None:
        if value == "0" * 32:
            raise ValueError("native invocation identity must be observed or explicitly unavailable")
        return value
