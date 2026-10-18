from enum import StrEnum


class ToolStatus(StrEnum):
    READY = "READY"
    MISSING = "MISSING"
    BROKEN = "BROKEN"
    OPTIONAL_MISSING = "OPTIONAL_MISSING"


class ToolHealthReason(StrEnum):
    NONE = "NONE"
    TOOL_BROKEN = "TOOL_BROKEN"
    TOOL_MISSING = "TOOL_MISSING"
    OPTIONAL_TOOL_MISSING = "OPTIONAL_TOOL_MISSING"


class RuntimeSafetyStatus(StrEnum):
    UNPROVEN = "UNPROVEN"
    PROVEN_SAFE = "PROVEN_SAFE"
    DEGRADED = "DEGRADED"


class BackgroundFailureStage(StrEnum):
    SCHEDULE_SCHEDULER = "SCHEDULE_SCHEDULER"
    FLEET_RUN_SUPERVISOR = "FLEET_RUN_SUPERVISOR"


class SystemHealthStatus(StrEnum):
    READY = "READY"
    SETUP_REQUIRED = "SETUP_REQUIRED"
    DEGRADED = "DEGRADED"
