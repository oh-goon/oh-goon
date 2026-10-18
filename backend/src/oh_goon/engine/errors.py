from oh_goon.core.errors import OperationCancelled


class OperationCancelledError(OperationCancelled):
    """Engine-facing cancellation preserving the canonical domain failure semantics."""
