from enum import StrEnum


class FleetScope(StrEnum):
    INCLUDED = "INCLUDED"
    EXCLUDED = "EXCLUDED"


class FleetVerdict(StrEnum):
    UNKNOWN = "UNKNOWN"
    READY = "READY"
    READY_WITH_EXCLUSIONS = "READY_WITH_EXCLUSIONS"
    ATTENTION_REQUIRED = "ATTENTION_REQUIRED"

    @property
    def needs_attention(self) -> bool:
        return self is FleetVerdict.ATTENTION_REQUIRED

    @property
    def successful(self) -> bool:
        return self in {FleetVerdict.READY, FleetVerdict.READY_WITH_EXCLUSIONS}


class ResultFreshness(StrEnum):
    CURRENT = "CURRENT"
    STALE = "STALE"


class ReadinessState(StrEnum):
    NOT_CHECKED = "NOT_CHECKED"
    READY = "READY"
    STALE = "STALE"
    MANUAL_ACTION_REQUIRED = "MANUAL_ACTION_REQUIRED"
    FAILED = "FAILED"

    @property
    def can_update(self) -> bool:
        return self is ReadinessState.READY

    @property
    def needs_attention(self) -> bool:
        return self in {ReadinessState.MANUAL_ACTION_REQUIRED, ReadinessState.FAILED}


class RiskLevel(StrEnum):
    NONE = "NONE"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
