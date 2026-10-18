from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from oh_goon.core.models.base import ImmutableModel


class FleetContentMaintenanceReceipt(ImmutableModel):
    outcome: Literal["MAINTAINED", "NOOP", "REFUSED"]
    reason: str | None = Field(default=None, max_length=500)
    before_bytes: int = Field(ge=0)
    after_bytes: int = Field(ge=0)
    before_objects: int = Field(ge=0)
    after_objects: int = Field(ge=0)
    removed_bytes: int = Field(ge=0)
    removed_objects: int = Field(ge=0)
    protected_objects: int = Field(ge=0)
    idle_protected_objects: int = Field(ge=0)
    budget_satisfied: bool
    removal_limit_reached: bool
    recovered_deletions: int = Field(default=0, ge=0)
    removed_orphan_lifecycle: int = Field(default=0, ge=0)
    max_retained_bytes: int = Field(ge=0)
    max_retained_objects: int = Field(ge=0)
    min_idle_ns: int = Field(ge=0)
    max_removed_bytes: int = Field(ge=0)
    max_removed_objects: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_receipt(self) -> "FleetContentMaintenanceReceipt":
        if self.outcome == "REFUSED" and not (self.reason or "").strip():
            raise ValueError("REFUSED maintenance receipt requires reason")
        if self.after_bytes > self.before_bytes:
            raise ValueError("maintenance cannot increase retained byte census")
        if self.after_objects > self.before_objects:
            raise ValueError("maintenance cannot increase retained object census")
        return self


class FleetContentMaintenanceFact(ImmutableModel):
    repository_id: str = Field(min_length=1)
    operation_id: str = Field(min_length=1)
    receipt: FleetContentMaintenanceReceipt


class GameTapeStorage(ImmutableModel):
    pressure: Literal["NO_EVIDENCE", "UNDER_BUDGET", "OVER_BUDGET", "REFUSED"]
    maintenance_cuts: int = Field(ge=0)
    peak_observed_bytes: int | None = Field(default=None, ge=0)
    latest_retained_bytes: int | None = Field(default=None, ge=0)
    latest_retained_objects: int | None = Field(default=None, ge=0)
    removed_bytes: int = Field(default=0, ge=0)
    removed_objects: int = Field(default=0, ge=0)
    recovered_deletions: int = Field(default=0, ge=0)
    removed_orphan_lifecycle: int = Field(default=0, ge=0)
    final_budget_satisfied: bool | None = None
    removal_limit_reached: bool = False
    refusal_reason: str | None = None
