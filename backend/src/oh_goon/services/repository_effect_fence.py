from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from oh_goon.core.errors import RepositoryStateChanged
from oh_goon.core.models.provenance import RepositoryStateFingerprint
from oh_goon.engine.errors import OperationCancelledError
from oh_goon.engine.git import GitService


class RepositoryEffectFence:
    """Check cancellation and native Git/manifest preconditions before effects.

    This legacy witness does not prove complete source stability. Captured source
    and its Hashmarks content identity retain their separate admission owners.
    """

    def __init__(self, git: GitService) -> None:
        self._git = git

    def verify(
        self,
        repository: Path,
        baseline: RepositoryStateFingerprint,
        cancel_requested: Callable[[], bool],
    ) -> None:
        if cancel_requested():
            raise OperationCancelledError("Operation cancellation requested before effects started")
        current = self._git.fingerprint(repository, cancel_requested)
        if current.state_id != baseline.state_id:
            raise RepositoryStateChanged(
                "Repository changed after its durable baseline was captured; effects were not started"
            )
        if cancel_requested():
            raise OperationCancelledError("Operation cancellation requested before effects started")
