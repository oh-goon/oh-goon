from enum import StrEnum


class GitBranchState(StrEnum):
    NAMED = "NAMED"
    DETACHED = "DETACHED"
    UNAVAILABLE = "UNAVAILABLE"


class WorktreeState(StrEnum):
    UNKNOWN = "UNKNOWN"
    CLEAN = "CLEAN"
    DIRTY = "DIRTY"
    NOT_GIT = "NOT_GIT"


class GitFinalState(StrEnum):
    CLEAN = "CLEAN"
    UNEXPECTED_CHANGES = "UNEXPECTED_CHANGES"
    CHANGED_BY_UPDATE = "CHANGED_BY_UPDATE"
