"""Authored task order and frozen selection for a writable Schedule."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Annotated

from pydantic import AfterValidator, Field, field_validator, model_validator

from oh_goon.core.models.agent_providers import ResolvedNativeAgent
from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.models.branch_advance import BranchAdvanceId, BranchRunId
from oh_goon.core.models.fleet_plan import MAX_AGENT_TASK_CHARS
from oh_goon.core.models.parallel_agent_work import parallel_native_selection_id
from oh_goon.core.product_identity import stable_id

MAX_WRITABLE_SCHEDULE_TASKS = 32
_ADVANCE_PREFIX = "schedule-branch"
ScheduleTaskId = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")]


def writable_schedule_run_id(generation_id: str, task_index: int) -> str:
    return stable_id("schedule-run", [generation_id, task_index])


def writable_schedule_advance_id(schedule_run_id: str) -> str:
    return stable_id(_ADVANCE_PREFIX, schedule_run_id)


def is_scheduled_branch_advance_id(advance_id: str) -> bool:
    prefix = f"{_ADVANCE_PREFIX}-"
    return (
        advance_id.startswith(prefix)
        and re.fullmatch(r"[0-9a-f]{64}", advance_id.removeprefix(prefix)) is not None
    )


def writable_native_selection_id(selected: ResolvedNativeAgent) -> str:
    return stable_id(
        "writable-native-selection",
        [parallel_native_selection_id(selected), selected.backend_configuration_id],
    )


class WritableScheduleTask(ImmutableModel):
    task_id: ScheduleTaskId
    prompt: str = Field(min_length=1, max_length=MAX_AGENT_TASK_CHARS, repr=False)

    @field_validator("prompt")
    @classmethod
    def validate_prompt(cls, value: str) -> str:
        if not value.strip() or "\x00" in value:
            raise ValueError("Scheduled task must be nonblank and cannot contain NUL")
        value.encode("utf-8")
        return value

    @property
    def prompt_sha256(self) -> str:
        return hashlib.sha256(self.prompt.encode("utf-8")).hexdigest()


def _require_unique_tasks(
    tasks: tuple[WritableScheduleTask, ...],
) -> tuple[WritableScheduleTask, ...]:
    if len({task.task_id for task in tasks}) != len(tasks):
        raise ValueError("Writable Schedule task IDs must be unique")
    return tasks


WritableScheduleTasks = Annotated[
    tuple[WritableScheduleTask, ...],
    Field(min_length=1, max_length=MAX_WRITABLE_SCHEDULE_TASKS),
    AfterValidator(_require_unique_tasks),
]


class ApprovedTaskDocument(ImmutableModel):
    """One operator-selected document in its original directory."""

    path: Path
    directory_device: int = Field(ge=0)
    directory_inode: int = Field(gt=0)

    @field_validator("path")
    @classmethod
    def validate_path(cls, path: Path) -> Path:
        if not path.is_absolute() or not path.name or ".." in path.parts or "\x00" in str(path):
            raise ValueError("Approved task document requires an absolute file path")
        return path


class ApprovedTaskBatch(ImmutableModel):
    """The bounded, ordered contents of the selected task document."""

    tasks: Annotated[
        tuple[WritableScheduleTask, ...],
        Field(max_length=MAX_WRITABLE_SCHEDULE_TASKS),
        AfterValidator(_require_unique_tasks),
    ]


class WritableSchedulePolicy(ImmutableModel):
    seed_advance_id: BranchAdvanceId
    native_selection_id: str = Field(min_length=1, max_length=160)
    tasks: WritableScheduleTasks

    @model_validator(mode="after")
    def validate_seed(self) -> WritableSchedulePolicy:
        if is_scheduled_branch_advance_id(self.seed_advance_id):
            raise ValueError(
                "Writable Schedule seed requires an explicitly published branch receipt"
            )
        return self


class ScheduledWritableRound(ImmutableModel):
    task_index: int = Field(ge=0)
    task: WritableScheduleTask
    advance_id: BranchAdvanceId
    predecessor_run_id: BranchRunId | None = None
    native_selection_id: str = Field(min_length=1, max_length=160)

    @model_validator(mode="after")
    def validate_predecessor(self) -> ScheduledWritableRound:
        if (self.task_index == 0) != (self.predecessor_run_id is None):
            raise ValueError("Writable round must bind its seed or prior Run")
        if is_scheduled_branch_advance_id(self.advance_id) != (self.task_index > 0):
            raise ValueError(
                "Writable round must use its exact seed or scheduled branch advance ID"
            )
        return self
