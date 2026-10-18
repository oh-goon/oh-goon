from enum import StrEnum


class FleetRunSource(StrEnum):
    CICD = "CICD"
    SCHEDULED = "SCHEDULED"
    SHARED_RUN = "SHARED_RUN"
    MANUAL = "MANUAL"


class FleetRunStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    CANCELLING = "CANCELLING"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"

    @property
    def terminal(self) -> bool:
        return self in {FleetRunStatus.COMPLETED, FleetRunStatus.CANCELLED, FleetRunStatus.FAILED}

    @property
    def active(self) -> bool:
        return self in {FleetRunStatus.RUNNING, FleetRunStatus.CANCELLING}

    @property
    def can_request_cancel(self) -> bool:
        return self in {FleetRunStatus.QUEUED, FleetRunStatus.RUNNING}

    @property
    def cancel_request_satisfied(self) -> bool:
        return self in {FleetRunStatus.CANCELLING, FleetRunStatus.CANCELLED}

    @property
    def accepts_repository_completion(self) -> bool:
        return self in {FleetRunStatus.RUNNING, FleetRunStatus.CANCELLING}

    @property
    def receipt_timestamp_contract(self) -> tuple[tuple[str, ...], tuple[str, ...]]:
        return {
            FleetRunStatus.QUEUED: ((), ()),
            FleetRunStatus.RUNNING: (("started_at",), ("started_at",)),
            FleetRunStatus.CANCELLING: (
                ("cancel_requested_at",),
                ("started_at", "cancel_requested_at"),
            ),
            FleetRunStatus.COMPLETED: (
                ("started_at", "finished_at"),
                ("started_at", "finished_at"),
            ),
            FleetRunStatus.CANCELLED: (
                ("cancel_requested_at", "finished_at"),
                ("started_at", "cancel_requested_at", "finished_at"),
            ),
            FleetRunStatus.FAILED: (
                ("finished_at",),
                ("started_at", "cancel_requested_at", "finished_at"),
            ),
        }[self]


class FleetRunOutcome(StrEnum):
    IN_PROGRESS = "IN_PROGRESS"
    SUCCESS = "SUCCESS"
    ATTENTION_REQUIRED = "ATTENTION_REQUIRED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"

    @property
    def needs_attention(self) -> bool:
        return self in {FleetRunOutcome.ATTENTION_REQUIRED, FleetRunOutcome.FAILED}


class FleetRunTerminalIntent(StrEnum):
    COMPLETE = "COMPLETE"
    CANCEL = "CANCEL"
    FAIL = "FAIL"


class FleetRunFailureStage(StrEnum):
    SUBMISSION = "SUBMISSION"
    EXECUTION = "EXECUTION"
    RECOVERY = "RECOVERY"


class FleetRunEventType(StrEnum):
    RUN_ADMITTED = "RUN_ADMITTED"
    RUN_STARTED = "RUN_STARTED"
    REPOSITORY_STARTED = "REPOSITORY_STARTED"
    STEP_REACHED = "STEP_REACHED"
    STEP_STARTED = "STEP_STARTED"
    STEP_ATTEMPT_CREATED = "STEP_ATTEMPT_CREATED"
    PROCESS_STARTING = "PROCESS_STARTING"
    PROCESS_STARTED = "PROCESS_STARTED"
    TERMINATION_REQUESTED = "TERMINATION_REQUESTED"
    PROCESS_STATE_UNKNOWN = "PROCESS_STATE_UNKNOWN"
    PROCESS_EXITED = "PROCESS_EXITED"
    DESCENDANTS_QUIESCENT = "DESCENDANTS_QUIESCENT"
    OUTPUT_FINALIZED = "OUTPUT_FINALIZED"
    OUTPUT_ABANDONED = "OUTPUT_ABANDONED"
    OUTPUT_QUARANTINED = "OUTPUT_QUARANTINED"
    STEP_SUCCEEDED = "STEP_SUCCEEDED"
    STEP_FAILED = "STEP_FAILED"
    STEP_SKIPPED = "STEP_SKIPPED"
    STEP_CANCELLED = "STEP_CANCELLED"
    REPOSITORY_COMPLETED = "REPOSITORY_COMPLETED"
    STORAGE_MAINTENANCE_RECORDED = "STORAGE_MAINTENANCE_RECORDED"
    RUN_CANCEL_REQUESTED = "RUN_CANCEL_REQUESTED"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
    RECONCILIATION_STARTED = "RECONCILIATION_STARTED"
    RECONCILIATION_COMPLETED = "RECONCILIATION_COMPLETED"
    RECONCILIATION_FAILED = "RECONCILIATION_FAILED"
    RUN_COMPLETED = "RUN_COMPLETED"
    RUN_CANCELLED = "RUN_CANCELLED"
    RUN_FAILED = "RUN_FAILED"
