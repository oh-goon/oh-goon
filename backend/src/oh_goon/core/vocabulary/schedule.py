from enum import StrEnum


class ScheduleState(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"


class ScheduleExecutionStatus(StrEnum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    ATTENTION_REQUIRED = "ATTENTION_REQUIRED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

    @property
    def terminal(self) -> bool:
        return self is not ScheduleExecutionStatus.RUNNING


class ScheduleRepositoryRunStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESSFUL = "SUCCESSFUL"
    ATTENTION_REQUIRED = "ATTENTION_REQUIRED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    NOT_STARTED = "NOT_STARTED"

    @property
    def terminal(self) -> bool:
        return self not in {
            ScheduleRepositoryRunStatus.PENDING,
            ScheduleRepositoryRunStatus.RUNNING,
        }


class ScheduleOccurrenceFilter(StrEnum):
    ALL = "ALL"
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESSFUL = "SUCCESSFUL"
    ATTENTION_REQUIRED = "ATTENTION_REQUIRED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    NOT_STARTED = "NOT_STARTED"


class ScheduleFailureStage(StrEnum):
    FLEET_RUN = "FLEET_RUN"
    ADMISSION = "ADMISSION"
    ADVANCE = "ADVANCE"
