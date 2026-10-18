from enum import StrEnum


class WorkspaceKind(StrEnum):
    """Canonical durable workspace materialization kind."""

    READ_SNAPSHOT = "READ_SNAPSHOT"
    WORKTREE = "WORKTREE"


class WorkspaceLifecycleState(StrEnum):
    """Backend-owned durable lifecycle for one workspace identity."""

    REQUESTED = "REQUESTED"
    ALLOCATED = "ALLOCATED"
    READY = "READY"
    IN_USE = "IN_USE"
    FINALIZED = "FINALIZED"
    RELEASED = "RELEASED"
    FAILED = "FAILED"
