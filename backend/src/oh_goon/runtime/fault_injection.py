from __future__ import annotations

from enum import StrEnum
from typing import Protocol


class FaultPoint(StrEnum):
    AFTER_DIRECT_ATTEMPT_CLAIM = "AFTER_DIRECT_ATTEMPT_CLAIM"
    AFTER_DIRECT_EFFECT_STARTED = "AFTER_DIRECT_EFFECT_STARTED"
    AFTER_REPOSITORY_CLAIMED = "AFTER_REPOSITORY_CLAIMED"
    AFTER_REPOSITORY_EFFECT_STARTED = "AFTER_REPOSITORY_EFFECT_STARTED"
    BEFORE_RUNNER = "BEFORE_RUNNER"
    AFTER_RUNNER = "AFTER_RUNNER"
    BEFORE_OPERATION_COMMIT = "BEFORE_OPERATION_COMMIT"


class FaultInjector(Protocol):
    def hit(self, point: FaultPoint) -> None: ...


class NoFaultInjector:
    """Production default: deterministic fault injection is opt-in through composition."""

    def hit(self, point: FaultPoint) -> None:
        del point


class RaisingFaultInjector:
    """Test/dev harness that simulates process failure at declared effect boundaries."""

    def __init__(self, points: set[FaultPoint]) -> None:
        self._points = frozenset(points)

    def hit(self, point: FaultPoint) -> None:
        if point in self._points:
            raise RuntimeError(f"Injected fault at {point.value}")
