from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from threading import Lock

from oh_goon.runtime.recovery_evidence import ExecutionRecoveryEvent


@dataclass(frozen=True, slots=True)
class SQLiteAccessDiagnostic:
    logical_operation: str
    native_opens: int
    observer_opens: int
    transactions: int
    reproof_requested: int
    reproof_succeeded: int
    reproof_failed: int


_METRICS = (
    "native_opens",
    "observer_opens",
    "transactions",
    "reproof_requested",
    "reproof_succeeded",
    "reproof_failed",
)


class SQLiteAccessDiagnostics:
    """Passive, opt-in process-local counters; never durable SQLite authority."""

    def __init__(self) -> None:
        self._enabled = False
        self._lock = Lock()
        self._counts: dict[str, dict[str, int]] = defaultdict(
            lambda: {name: 0 for name in _METRICS}
        )

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled

    def record(self, logical_operation: str, metric: str) -> None:
        if not self._enabled:
            return
        if metric not in _METRICS:
            raise ValueError(f"unknown SQLite diagnostic metric: {metric}")
        with self._lock:
            self._counts[logical_operation][metric] += 1

    def record_recovery(self, event: ExecutionRecoveryEvent, logical_operation: str | None) -> None:
        if event.kind != "sqlite_reproof":
            return
        metric = {
            "requested": "reproof_requested",
            "succeeded": "reproof_succeeded",
            "failed": "reproof_failed",
        }.get(event.outcome)
        if metric is not None:
            self.record(logical_operation or event.action, metric)

    def snapshot(self) -> tuple[SQLiteAccessDiagnostic, ...]:
        with self._lock:
            return tuple(
                SQLiteAccessDiagnostic(logical_operation=operation, **metrics)
                for operation, metrics in sorted(self._counts.items())
            )
