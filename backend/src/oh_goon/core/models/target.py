from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from oh_goon.core.execution_lock_identity import build_execution_lock
from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.models.caller_input_file import (
    CallerInputFileIdentity,
    ScheduledCallerFileReceipt,
)
from oh_goon.core.models.execution_identity import (
    ExecutionIdentity,
    ResolvedExecutionReference,
)
from oh_goon.core.models.fleet_plan import ResolvedFleetPlan, ResolvedLocalSearchAgentStep
from oh_goon.core.models.goon_plan_source import FleetPlanSource
from oh_goon.core.vocabulary import MigrationMode, UvRecipeId

_TASK_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")


class UvRecipeTarget(ImmutableModel):
    """Typed UV command intent. Arguments are argv tokens; no shell parsing is used."""

    kind: Literal["recipe"]
    recipe: UvRecipeId
    task: str | None = None
    args: tuple[str, ...] = Field(default_factory=tuple, max_length=32)

    @field_validator("task")
    @classmethod
    def normalize_task(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @model_validator(mode="after")
    def validate_task(self) -> UvRecipeTarget:
        if self.recipe is UvRecipeId.RUN_TASK:
            if not self.task or not _TASK_PATTERN.fullmatch(self.task):
                raise ValueError("uv run task must be one executable token (for example pytest)")
        elif self.task is not None or self.args:
            raise ValueError("Only RUN_TASK accepts task/args")
        return self


class MigrationTarget(ImmutableModel):
    """Adopt-UV migration intent. Source manager is derived per repository."""

    kind: Literal["migration"]
    mode: MigrationMode = MigrationMode.ADOPT


class AgentStepDeliveryReference(ImmutableModel):
    step_id: str = Field(min_length=1, max_length=240)
    handoff_identity: str = Field(min_length=1, max_length=160)
    delivery_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reference_id: str = Field(min_length=1, max_length=160)


class FleetPlanTarget(ImmutableModel):
    """Verified immutable execution target; constructed once before admission and transported unchanged."""

    kind: Literal["fleet_plan"]
    plan: ResolvedFleetPlan
    source: FleetPlanSource
    identity: ExecutionIdentity
    agent_deliveries: tuple[AgentStepDeliveryReference, ...] = ()
    caller_input_file: CallerInputFileIdentity | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    scheduled_caller_file: ScheduledCallerFileReceipt | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def validate_execution_identity(self) -> FleetPlanTarget:
        expected = build_execution_lock(self.plan).digest
        if self.identity.digest != expected:
            raise ValueError(
                "Fleet Plan execution identity does not match the resolved Plan semantics"
            )
        expected_agent = {
            step.step_id: (step.handoff_identity, step.delivery_sha256)
            for step in self.plan.steps
            if isinstance(step, ResolvedLocalSearchAgentStep)
        }
        found = {
            item.step_id: (item.handoff_identity, item.delivery_sha256)
            for item in self.agent_deliveries
        }
        if len(found) != len(self.agent_deliveries) or found != expected_agent:
            raise ValueError("Fleet Agent source reference does not match original Plan Step")
        if self.caller_input_file is not None and self.scheduled_caller_file is not None:
            raise ValueError(
                "live caller source and scheduled snapshot custody are mutually exclusive"
            )
        if (
            self.scheduled_caller_file is not None
            and self.scheduled_caller_file.execution_digest != self.identity.digest
        ):
            raise ValueError(
                "scheduled caller source is not bound to this resolved execution target"
            )
        return self

    @property
    def execution_digest(self) -> str:
        return self.identity.digest

    @property
    def execution_reference(self) -> ResolvedExecutionReference:
        return ResolvedExecutionReference(
            plan_id=self.plan.plan_id,
            plan_name=self.plan.name,
            source=self.source,
            identity=self.identity,
        )


type OperationTarget = Annotated[
    UvRecipeTarget | MigrationTarget | FleetPlanTarget,
    Field(discriminator="kind"),
]
