from enum import StrEnum


class Verdict(StrEnum):
    UNKNOWN = "UNKNOWN"
    NOT_CHECKED = "NOT_CHECKED"
    READY = "READY"
    UPDATED = "UPDATED"
    UNCHANGED = "UNCHANGED"
    FAILED = "FAILED"
    MANUAL_ACTION_REQUIRED = "MANUAL_ACTION_REQUIRED"
    EXCLUDED = "EXCLUDED"

    @property
    def needs_attention(self) -> bool:
        return self in {Verdict.FAILED, Verdict.MANUAL_ACTION_REQUIRED}

    @property
    def successful(self) -> bool:
        return self in {Verdict.READY, Verdict.UPDATED, Verdict.UNCHANGED, Verdict.EXCLUDED}

    @property
    def dependency_update_successful(self) -> bool:
        return self in {Verdict.UPDATED, Verdict.UNCHANGED}


class OperationAction(StrEnum):
    STATUS = "status"
    INIT = "init"
    PLAN = "plan"
    UPDATE = "update"
    VERIFY = "verify"
    PREPARE_REVIEW = "prepare-review"
    RECIPE = "recipe"
    ADOPT_UV = "adopt-uv"
    EXECUTE_PLAN = "execute-plan"


class EffectAttemptOwner(StrEnum):
    DIRECT = "DIRECT"
    FLEET = "FLEET"


class EffectBoundaryKind(StrEnum):
    """Whether started work can escape Fleet-owned ephemeral execution state."""

    EXTERNAL = "EXTERNAL"
    CONTAINED = "CONTAINED"


class EffectAttemptStatus(StrEnum):
    CLAIMED = "CLAIMED"
    EFFECT_STARTED = "EFFECT_STARTED"
    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"
    PARTIALLY_APPLIED = "PARTIALLY_APPLIED"
    INDETERMINATE = "INDETERMINATE"


class ReconciliationOutcome(StrEnum):
    CONFIRMED_APPLIED = "CONFIRMED_APPLIED"
    CONFIRMED_NOT_APPLIED = "CONFIRMED_NOT_APPLIED"
    PARTIALLY_APPLIED = "PARTIALLY_APPLIED"
    INDETERMINATE = "INDETERMINATE"


class CheckState(StrEnum):
    PASSED = "PASSED"
    FAILED = "FAILED"


class ValidationState(StrEnum):
    VALID = "VALID"
    INVALID = "INVALID"


class AuditState(StrEnum):
    PASSED = "PASSED"
    VULNERABILITIES_FOUND = "VULNERABILITIES_FOUND"
    UNAVAILABLE = "UNAVAILABLE"
    NOT_SUPPORTED = "NOT_SUPPORTED"
    SKIPPED = "SKIPPED"
