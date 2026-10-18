from enum import StrEnum


class FailureImpact(StrEnum):
    BLOCKING = "blocking"
    NON_BLOCKING = "non_blocking"


class CanonicalResultStatus(StrEnum):
    SUCCESS = "SUCCESS"
    ATTENTION_REQUIRED = "ATTENTION_REQUIRED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
