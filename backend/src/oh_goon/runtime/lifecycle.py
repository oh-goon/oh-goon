from __future__ import annotations

from enum import StrEnum
from threading import Lock


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
