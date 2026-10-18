"""Transient execution services bound to one admitted repository operation attempt."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from oh_goon.core.models.effect_attempt import ClaimedRepositoryEffectAttempt
from oh_goon.engine.git import GitService


@dataclass(frozen=True, slots=True)
class OperationContext:
    """Native-engine transient services around one durable admitted effect attempt."""

    attempt: ClaimedRepositoryEffectAttempt
    timeout_seconds: float
    cancel_requested: Callable[[], bool] | None
    git: GitService
