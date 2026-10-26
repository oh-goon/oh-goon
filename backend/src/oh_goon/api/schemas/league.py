from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from oh_goon.core.models.fleet_plan_composition import PlanInputValue
from oh_goon.core.models.run_configuration import RunAgentSelection, RunRepairSelection


class TeamRunRequest(BaseModel):
    repository: Path
    fleet_run_id: str = Field(min_length=1)
    expected_pipeline_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_repository_instance_id: str = Field(min_length=1)
    inputs: dict[str, str] = Field(default_factory=dict)


class SharedPlanSaveRequest(BaseModel):
    content: str = Field(min_length=1)
    expected_revision: str | None = Field(default=None, min_length=64, max_length=64)


class SharedPlanFromTemplateRequest(BaseModel):
    template_key: str = Field(min_length=3, max_length=128)
    expected_template_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class SharedPlanRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan_key: str = Field(min_length=1, max_length=64)
    expected_plan_id: str = Field(min_length=1)
    expected_plan_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_execution_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    repository_ids: tuple[str, ...] = Field(min_length=1)
    repository_instance_ids: dict[str, str] = Field(min_length=1)
    fleet_run_id: str = Field(min_length=1)
    inputs: dict[str, PlanInputValue] = Field(default_factory=dict)
    agent: RunAgentSelection | None = None

    @model_validator(mode="after")
    def validate_repository_instances(self) -> "SharedPlanRunRequest":
        if len(self.repository_ids) != len(set(self.repository_ids)):
            raise ValueError("repository_ids must be unique")
        if set(self.repository_instance_ids) != set(self.repository_ids):
            raise ValueError("repository_instance_ids must exactly match repository_ids")
        if any(not value for value in self.repository_instance_ids.values()):
            raise ValueError("repository_instance_ids values must be non-empty")
        return self


class SharedPlanLockPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan_key: str = Field(min_length=1, max_length=64)
    expected_revision: str = Field(min_length=64, max_length=64)
    inputs: dict[str, PlanInputValue] = Field(default_factory=dict)
    agent: RunAgentSelection | None = None
    repository_ids: tuple[str, ...] = ()


class SharedPlanPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan_key: str = Field(min_length=1, max_length=64)
    expected_revision: str = Field(min_length=64, max_length=64)
    repository_ids: tuple[str, ...] = Field(min_length=1)
    inputs: dict[str, PlanInputValue] = Field(default_factory=dict)
    agent: RunAgentSelection | None = None

    @model_validator(mode="after")
    def validate_repository_population(self) -> "SharedPlanPreviewRequest":
        if len(self.repository_ids) != len(set(self.repository_ids)):
            raise ValueError("repository_ids must be unique")
        return self


class SharedPlanRepairRequest(SharedPlanRunRequest):
    agent: RunAgentSelection
    repair: RunRepairSelection
    authorize_repair: Literal[True]

    @field_validator("authorize_repair", mode="before")
    @classmethod
    def explicit_authorization(cls, value: object) -> object:
        if value is not True:
            raise ValueError("Repair requires explicit boolean authorization")
        return value


class LeagueLineupSaveRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    repositories: tuple[Path, ...] = Field(min_length=1)
    expected_revision: str | None = Field(default=None, min_length=64, max_length=64)


class SafePlanInputDiagnostic(BaseModel):
    schema_name: Literal["oh-goon.plan-validation-diagnostic/v1"] = Field(
        default="oh-goon.plan-validation-diagnostic/v1", alias="schema"
    )
    status: Literal["rejected"] = "rejected"
    stage: Literal["input_contract", "plan_resolution"]
    code: Literal[
        "UNKNOWN_INPUT",
        "MISSING_REQUIRED_INPUT",
        "INVALID_INPUT_VALUE",
        "INVALID_INPUT_DEFAULT",
        "UNOWNED_PROJECTED_INPUT",
        "AMBIGUOUS_PROJECTED_INPUT",
        "PLAN_RESOLUTION_REJECTED",
        "SOURCE_ACQUISITION_REQUIRED",
    ]
    inputs: list[str]
    safe_next_action: str
    execution: Literal["not_started_by_diagnostic"] = "not_started_by_diagnostic"


class SharedPlanInputInspection(BaseModel):
    schema_name: Literal["oh-goon.shared-plan-input-inspection/v1"] = Field(
        default="oh-goon.shared-plan-input-inspection/v1", alias="schema"
    )
    status: Literal["accepted", "rejected"]
    plan_key: str | None = None
    sha256: str | None = None
    plan_id: str | None = None
    diagnostic: SafePlanInputDiagnostic | None = None
    execution: Literal["not_started_by_inspection"] = "not_started_by_inspection"

    @model_validator(mode="after")
    def enforce_non_authorizing_inspection_contract(self) -> "SharedPlanInputInspection":
        if self.status == "accepted":
            if (
                not self.plan_key
                or not self.sha256
                or not self.plan_id
                or self.diagnostic is not None
            ):
                raise ValueError(
                    "Accepted input inspection requires exact source identity and no rejection"
                )
        elif (
            self.plan_key is not None
            or self.sha256 is not None
            or self.plan_id is not None
            or self.diagnostic is None
        ):
            raise ValueError("Rejected input inspection must contain only a safe diagnostic")
        return self
