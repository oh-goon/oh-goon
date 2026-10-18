from __future__ import annotations

import sqlite3
from collections.abc import Callable

from oh_goon.core.models.effect_attempt import RepositoryEffectAttempt
from oh_goon.runtime.effect_attempts import RepositoryEffectAttemptStore
from oh_goon.runtime.execution import ExecutionSession


def mutate_pre_effect_attempt(
    session: ExecutionSession,
    attempts: RepositoryEffectAttemptStore,
    operation_id: str,
    mutate: Callable[[sqlite3.Connection], RepositoryEffectAttempt],
) -> RepositoryEffectAttempt:
    """Replay only an exact pre-runner attempt mutation after a lost commit response."""
    intended: RepositoryEffectAttempt | None = None

    def capture(connection: sqlite3.Connection) -> RepositoryEffectAttempt:
        nonlocal intended
        intended = mutate(connection)
        return intended

    try:
        return session.mutate(capture)
    except BaseException as error:
        if intended is None:
            raise
        try:
            canonical = attempts.get(operation_id)
        except BaseException as verify_error:
            raise error from verify_error
        if canonical == intended:
            return canonical
        raise
