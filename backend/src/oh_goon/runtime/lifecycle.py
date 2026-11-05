from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
from threading import TIMEOUT_MAX, Lock

from oh_goon.core.errors import RuntimeQuiescing


@dataclass(frozen=True, slots=True)
class RuntimeDrainPolicy:
    """Resolved process shutdown budgets; they do not declare any Run result."""

    drain_seconds: float | None = 30.0
    cancel_grace_seconds: float = 5.0
    termination_grace_seconds: float = 1.0
    containment_timeout_seconds: float = 10.0

    def __post_init__(self) -> None:
        for name, value in (
            ("drain", self.drain_seconds),
            ("cancel", self.cancel_grace_seconds),
            ("termination", self.termination_grace_seconds),
            ("containment", self.containment_timeout_seconds),
        ):
            if value is None and name == "drain":
                continue
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not isfinite(value)
                or value < 0
                or value > TIMEOUT_MAX
            ):
                raise ValueError("Runtime shutdown budgets exceed native finite wait bounds")
        if self.termination_grace_seconds > 60:
            raise ValueError("Runtime native termination grace cannot exceed 60 seconds")
        if self.join_timeout_seconds is not None and self.join_timeout_seconds > TIMEOUT_MAX:
            raise ValueError("Runtime shutdown join budget exceeds the native wait bound")

    @property
    def join_timeout_seconds(self) -> float | None:
        if self.drain_seconds is None:
            return None
        return (
            self.drain_seconds
            + self.cancel_grace_seconds
            + self.termination_grace_seconds
            + self.containment_timeout_seconds
        )


class RuntimeLifecycleState(StrEnum):
    BUILT = "BUILT"
    RUNNING = "RUNNING"
    QUIESCING = "QUIESCING"
    CLOSED = "CLOSED"


class RuntimeLifecycle:
    """Small process-lifecycle state machine; closed runtimes are never reopened."""

    def __init__(self) -> None:
        self._state = RuntimeLifecycleState.BUILT
        self._lock = Lock()
        self._shutdown_lock = Lock()

    @contextmanager
    def shutdown_scope(self, *, timeout: float | None = None) -> Generator[None, None, None]:
        """Serialize cleanup callers while allowing an incomplete shutdown to be retried."""
        acquired = (
            self._shutdown_lock.acquire()
            if timeout is None
            else self._shutdown_lock.acquire(timeout=timeout)
        )
        if not acquired:
            raise RuntimeQuiescing(
                "Runtime lifecycle transition remains in progress; completion is unverified"
            )
        try:
            yield
        finally:
            self._shutdown_lock.release()

    @property
    def state(self) -> RuntimeLifecycleState:
        with self._lock:
            return self._state

    def mark_running(self) -> RuntimeLifecycleState:
        with self._lock:
            if self._state is RuntimeLifecycleState.BUILT:
                self._state = RuntimeLifecycleState.RUNNING
            return self._state

    def begin_quiesce(self) -> bool:
        with self._lock:
            if self._state in {RuntimeLifecycleState.QUIESCING, RuntimeLifecycleState.CLOSED}:
                return False
            self._state = RuntimeLifecycleState.QUIESCING
            return True

    def close(self) -> None:
        with self._lock:
            self._state = RuntimeLifecycleState.CLOSED
