from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from oh_goon.core.models.background import BackgroundFailure
from oh_goon.core.models.diagnostic import DiagnosticEvent
from oh_goon.core.ports.diagnostics import DiagnosticEmitter
from oh_goon.core.vocabulary import BackgroundFailureStage


@dataclass(frozen=True, slots=True)
class ScheduleSchedulerSnapshot:
    running: bool
    ticks: int
    failures: int
    last_failure: BackgroundFailure | None


class ScheduleScheduler:
    """Own the process-local Schedule clock; execution semantics stay in ScheduleService."""

    def __init__(
        self,
        tick: Callable[[], None],
        *,
        interval_seconds: float = 5.0,
        diagnostics: DiagnosticEmitter | None = None,
    ) -> None:
        self._tick = tick
        self._interval = interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lifecycle_lock = threading.Lock()
        self._stats_lock = threading.Lock()
        self._stats_changed = threading.Condition(self._stats_lock)
        self._ticks = 0
        self._failures = 0
        self._last_failure: BackgroundFailure | None = None
        self._tick_generation = 0
        self._active_ticks = 0
        self._accepting_ticks = True
        self._diagnostics = diagnostics

    def start(self) -> None:
        with self._lifecycle_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            with self._stats_changed:
                self._tick_generation += 1
                self._accepting_ticks = True
            thread = threading.Thread(
                target=self._loop,
                name="oh-goon-schedules",
                daemon=False,
            )
            self._thread = thread
            thread.start()

    def stop(self) -> None:
        # Serialize start/stop over the full drain boundary so a concurrent start cannot
        # publish a replacement worker while the current generation is still draining.
        with self._lifecycle_lock:
            self._stop.set()
            with self._stats_changed:
                self._accepting_ticks = False
            thread = self._thread
            deadline = time.monotonic() + max(1.0, self._interval * 2)
            while self._work_is_active(thread):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    exc = RuntimeError(
                        "schedule scheduler worker did not stop before the bounded shutdown deadline"
                    )
                    with self._stats_changed:
                        # Supersede the health projection owned by any pre-stop tick. The
                        # tick may finish later for historical accounting, but it cannot
                        # clear or replace this newer shutdown failure.
                        self._tick_generation += 1
                        self._failures += 1
                        self._last_failure = BackgroundFailure.from_exception(
                            exc,
                            stage=BackgroundFailureStage.SCHEDULE_SCHEDULER,
                        )
                    if self._diagnostics is not None:
                        self._diagnostics.emit(
                            DiagnosticEvent.from_exception(
                                exc,
                                source="schedule_scheduler",
                                owner="ScheduleScheduler",
                                operation="stop",
                                execution_affected=True,
                                failure_class="background_worker",
                            )
                        )
                    # Shutdown cannot claim CLOSED while this pre-shutdown lease still
                    # owns mutation authority. The caller remains QUIESCING and may
                    # surface/handle the incomplete shutdown explicitly.
                    raise exc
                if thread is not None and thread.is_alive():
                    thread.join(timeout=min(0.1, remaining))
                else:
                    with self._stats_changed:
                        if self._active_ticks:
                            self._stats_changed.wait(timeout=min(0.1, remaining))
            if self._thread is thread:
                self._thread = None

    def _work_is_active(self, thread: threading.Thread | None) -> bool:
        if thread is not None and thread.is_alive():
            return True
        with self._stats_changed:
            return self._active_ticks > 0

    def tick(self) -> None:
        with self._stats_changed:
            if not self._accepting_ticks:
                return
            generation = self._tick_generation
            self._active_ticks += 1
        failure: BackgroundFailure | None = None
        failure_exception: Exception | None = None
        try:
            self._tick()
        except Exception as exc:
            failure_exception = exc
            failure = BackgroundFailure.from_exception(
                exc,
                stage=BackgroundFailureStage.SCHEDULE_SCHEDULER,
            )
        finally:
            with self._stats_changed:
                if failure is None:
                    self._ticks += 1
                    if generation == self._tick_generation:
                        self._last_failure = None
                else:
                    self._failures += 1
                    if generation == self._tick_generation:
                        self._last_failure = failure
                self._active_ticks -= 1
                self._stats_changed.notify_all()
        if failure_exception is not None and self._diagnostics is not None:
            self._diagnostics.emit(
                DiagnosticEvent.from_exception(
                    failure_exception,
                    source="schedule_scheduler",
                    owner="ScheduleScheduler",
                    operation="tick",
                    execution_affected=True,
                    failure_class="background_worker",
                )
            )

    def snapshot(self) -> ScheduleSchedulerSnapshot:
        with self._lifecycle_lock:
            running = self._thread is not None and self._thread.is_alive()
            with self._stats_lock:
                return ScheduleSchedulerSnapshot(
                    running=running,
                    ticks=self._ticks,
                    failures=self._failures,
                    last_failure=self._last_failure,
                )

    def _loop(self) -> None:
        while not self._stop.wait(self._interval):
            self.tick()
