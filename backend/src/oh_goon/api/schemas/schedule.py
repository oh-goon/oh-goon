from pydantic import BaseModel, Field, field_validator

from oh_goon.core.vocabulary import ScheduleState


class ScheduleRunRequest(BaseModel):
    schedule_run_id: str = Field(min_length=1)
    expected_generation_id: str = Field(min_length=1)
    expected_record_version: int = Field(ge=0)


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
    inputs: dict[str, str] = Field(default_factory=dict)

    @field_validator("repository_ids")
    @classmethod
    def canonicalize_repository_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("Schedule repository targets must be unique")
        return tuple(sorted(value))
