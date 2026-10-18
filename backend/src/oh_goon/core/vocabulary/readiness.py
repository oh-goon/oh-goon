from enum import StrEnum


class LockPresenceState(StrEnum):
    PRESENT = "PRESENT"
    MISSING = "MISSING"


class PythonPinReadiness(StrEnum):
    OPTIONAL = "OPTIONAL"
    PRESENT = "PRESENT"
    REQUIRED_MISSING = "REQUIRED_MISSING"
