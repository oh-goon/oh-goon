from __future__ import annotations

from enum import StrEnum

from pydantic import AwareDatetime, Field, model_validator

from oh_goon.core.models.base import ImmutableModel


class WatchdogDisposition(StrEnum):
    CONTINUE = "CONTINUE"
    SLOW = "SLOW"
    HEARTBEAT_LOST = "HEARTBEAT_LOST"
    PROCESS_LOST = "PROCESS_LOST"
    LEASE_LOST = "LEASE_LOST"
    STALLED = "STALLED"
    HARD_TIMEOUT = "HARD_TIMEOUT"
    YIELD_REQUESTED = "YIELD_REQUESTED"
    YIELD_DEADLINE_EXCEEDED = "YIELD_DEADLINE_EXCEEDED"


class RuntimeSupervisionPolicy(ImmutableModel):
    """Resolved supervision policy for one physical execution attempt."""

    slow_after_seconds: int | None = Field(default=None, ge=1, le=86_400)
    max_runtime_seconds: int = Field(ge=1, le=86_400)
    heartbeat_interval_seconds: int = Field(ge=1, le=3_600)
    heartbeat_timeout_seconds: int = Field(ge=1, le=86_400)
    progress_start_grace_seconds: int = Field(default=0, ge=0, le=86_400)
    no_progress_timeout_seconds: int | None = Field(default=None, ge=1, le=86_400)
    termination_grace_seconds: int = Field(ge=0, le=3_600)
    quiescence_timeout_seconds: int = Field(ge=1, le=3_600)
    yield_deadline_seconds: int = Field(ge=1, le=86_400)
    policy_revision: str = Field(min_length=1, max_length=120)

    @model_validator(mode="after")
    def _ordered_boundaries(self) -> "RuntimeSupervisionPolicy":
        if self.slow_after_seconds is not None and self.slow_after_seconds >= self.max_runtime_seconds:
            raise ValueError("slow_after_seconds must be below max_runtime_seconds")
        if self.no_progress_timeout_seconds is not None and self.no_progress_timeout_seconds >= self.max_runtime_seconds:
            raise ValueError("no_progress_timeout_seconds must be below max_runtime_seconds")
        if self.progress_start_grace_seconds >= self.max_runtime_seconds:
            raise ValueError("progress_start_grace_seconds must be below max_runtime_seconds")
        if self.heartbeat_timeout_seconds < self.heartbeat_interval_seconds:
            raise ValueError("heartbeat_timeout_seconds must not be below heartbeat_interval_seconds")
        return self


class SemanticProgress(ImmutableModel):
    execution_id: str = Field(min_length=1)
    generation: int = Field(ge=1)
    sequence: int = Field(ge=1)
    observed_at: AwareDatetime
    work_unit: str | None = Field(default=None, max_length=240)
    phase: str | None = Field(default=None, max_length=120)
    marker: str | None = Field(default=None, max_length=240)


class RuntimeObservation(ImmutableModel):
    execution_id: str = Field(min_length=1)
    generation: int = Field(ge=1)
    started_at: AwareDatetime
    observed_at: AwareDatetime
    last_heartbeat_at: AwareDatetime | None = None
    lease_valid: bool
    process_alive: bool
    last_progress: SemanticProgress | None = None
    completed_at: AwareDatetime | None = None
    yield_requested_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def _consistent_identity(self) -> "RuntimeObservation":
        if self.observed_at < self.started_at:
            raise ValueError("observed_at cannot precede started_at")
        if self.last_heartbeat_at is not None and not (self.started_at <= self.last_heartbeat_at <= self.observed_at):
            raise ValueError("heartbeat timestamp is outside the observation window")
        if self.last_progress is not None:
            if self.last_progress.execution_id != self.execution_id or self.last_progress.generation != self.generation:
                raise ValueError("semantic progress belongs to another execution generation")
            if self.last_progress.observed_at < self.started_at or self.last_progress.observed_at > self.observed_at:
                raise ValueError("semantic progress timestamp is outside the observation window")
        if self.completed_at is not None and not (self.started_at <= self.completed_at <= self.observed_at):
            raise ValueError("completion timestamp is outside the observation window")
        if self.yield_requested_at is not None and not (self.started_at <= self.yield_requested_at <= self.observed_at):
            raise ValueError("yield request timestamp is outside the observation window")
        return self


class WatchdogDecision(ImmutableModel):
    disposition: WatchdogDisposition
    policy_revision: str
    elapsed_seconds: int = Field(ge=0)
    progress_idle_seconds: int = Field(ge=0)
    heartbeat_idle_seconds: int | None = Field(default=None, ge=0)
    termination_required: bool = False
    output_fenced: bool = False
    reason: str = Field(min_length=1)
