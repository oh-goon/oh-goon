from enum import StrEnum


class MigrationMode(StrEnum):
    ADOPT = "ADOPT"


class MigrationSource(StrEnum):
    UV = "UV"
    PYPROJECT = "PYPROJECT"
    PIP_REQUIREMENTS = "PIP_REQUIREMENTS"
    PIP_TOOLS = "PIP_TOOLS"
    POETRY = "POETRY"
    PDM = "PDM"
    RYE = "RYE"
    UNKNOWN = "UNKNOWN"


class MigrationReadiness(StrEnum):
    ALREADY_UV = "ALREADY_UV"
    SAFE_AUTOMATIC = "SAFE_AUTOMATIC"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    UNSUPPORTED = "UNSUPPORTED"
