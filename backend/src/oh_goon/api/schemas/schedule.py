from pydantic import BaseModel, ConfigDict, Field, field_validator

from oh_goon.core.models.branch_advance import BranchAdvanceId
from oh_goon.core.models.fleet_plan_composition import PlanInputValue
from oh_goon.core.models.writable_schedule import WritableScheduleTasks
from oh_goon.core.vocabulary import ScheduleState


class ScheduleRunRequest(BaseModel):
    schedule_run_id: str = Field(min_length=1)
    expected_generation_id: str = Field(min_length=1)
    expected_record_version: int = Field(ge=0)


class RetainedReviewScheduleCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fleet_run_id: str = Field(min_length=1, max_length=160)
    name: str | None = Field(default=None, min_length=1, max_length=80)
    interval_minutes: int = Field(ge=1, le=10080)


class WritableScheduleCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    seed_advance_id: BranchAdvanceId
    tasks: WritableScheduleTasks
    name: str | None = Field(default=None, min_length=1, max_length=80)
    interval_minutes: int = Field(ge=1, le=10080)


class SharedPlanScheduleCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    repository_ids: tuple[str, ...] = Field(min_length=1)
    expected_repository_instance_ids: dict[str, str] = Field(min_length=1)
    plan_key: str = Field(min_length=1, max_length=64)
    expected_plan_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_plan_id: str = Field(min_length=1)
    expected_execution_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    interval_minutes: int = Field(ge=1, le=10080)
    state: ScheduleState = ScheduleState.ACTIVE
    inputs: dict[str, PlanInputValue] = Field(default_factory=dict)

    @field_validator("repository_ids")
    @classmethod
    def canonicalize_repository_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("Schedule repository targets must be unique")
        return tuple(sorted(value))
