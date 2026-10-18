from __future__ import annotations

import pytest

from oh_goon.core.vocabulary import BackgroundFailureStage
from oh_goon.runtime.schedule_scheduler import ScheduleScheduler


class _Diagnostics:
    def __init__(self) -> None:
        self.events = []

    def emit(self, event) -> None:
        self.events.append(event)


def test_scheduler_emits_diagnostic_without_replacing_typed_background_failure() -> None:
    diagnostics = _Diagnostics()

    def fail() -> None:
        raise RuntimeError("scheduler tick failed")

    scheduler = ScheduleScheduler(fail, interval_seconds=60, diagnostics=diagnostics)
    scheduler.tick()

    snapshot = scheduler.snapshot()
    assert snapshot.failures == 1
    assert snapshot.last_failure is not None
    assert snapshot.last_failure.stage is BackgroundFailureStage.SCHEDULE_SCHEDULER
    assert len(diagnostics.events) == 1
    event = diagnostics.events[0]
    assert event.owner == "ScheduleScheduler"
    assert event.operation == "tick"
    assert event.execution_affected is True
    assert event.failure_class == "background_worker"


def test_stop_reports_worker_that_survives_bounded_shutdown(
    monkeypatch,
) -> None:
    import oh_goon.runtime.schedule_scheduler as module

    diagnostics = _Diagnostics()

    class StuckThread:
        def __init__(self) -> None:
            self.join_timeouts: list[float | None] = []

        def is_alive(self) -> bool:
            return True

        def join(self, timeout: float | None = None) -> None:
            self.join_timeouts.append(timeout)

    clock = iter((10.0, 10.2, 11.1))
    monkeypatch.setattr(module.time, "monotonic", lambda: next(clock))
    scheduler = ScheduleScheduler(lambda: None, interval_seconds=0.5, diagnostics=diagnostics)
    stuck = StuckThread()
    scheduler._thread = stuck  # type: ignore[assignment]

    with pytest.raises(
        RuntimeError, match="did not stop before the bounded shutdown deadline"
    ):
        scheduler.stop()

    snapshot = scheduler.snapshot()
    assert snapshot.running is True
    assert snapshot.failures == 1
    assert snapshot.last_failure is not None
    assert "did not stop before the bounded shutdown deadline" in snapshot.last_failure.message
    assert stuck.join_timeouts == [pytest.approx(0.1)]
    assert len(diagnostics.events) == 1
    assert diagnostics.events[0].operation == "stop"
    assert diagnostics.events[0].failure_class == "background_worker"


def test_stop_timeout_failure_survives_older_successful_tick(monkeypatch) -> None:
    import threading

    import oh_goon.runtime.schedule_scheduler as module

    real_monotonic = module.time.monotonic
    started = threading.Event()
    release = threading.Event()

    def blocked_tick() -> None:
        started.set()
        assert release.wait(timeout=1.0)

    scheduler = ScheduleScheduler(blocked_tick, interval_seconds=0.5)
    worker = threading.Thread(target=scheduler.tick)
    worker.start()
    assert started.wait(timeout=1.0)

    clock = iter((10.0, 10.2, 11.1))
    monkeypatch.setattr(module.time, "monotonic", lambda: next(clock))
    with pytest.raises(
        RuntimeError, match="did not stop before the bounded shutdown deadline"
    ):
        scheduler.stop()

    stop_failure = scheduler.snapshot().last_failure
    assert stop_failure is not None
    release.set()
    worker.join(timeout=1.0)
    assert not worker.is_alive()

    snapshot = scheduler.snapshot()
    assert snapshot.ticks == 1
    assert snapshot.failures == 1
    assert snapshot.last_failure == stop_failure

    # Only an explicitly newer scheduler generation may prove recovery and clear
    # the stop failure projection.
    monkeypatch.setattr(module.time, "monotonic", real_monotonic)
    scheduler.start()
    try:
        scheduler.tick()
        recovered = scheduler.snapshot()
        assert recovered.ticks == 2
        assert recovered.last_failure is None
    finally:
        scheduler.stop()


def test_stop_timeout_failure_survives_older_failing_tick(monkeypatch) -> None:
    import threading

    import oh_goon.runtime.schedule_scheduler as module

    started = threading.Event()
    release = threading.Event()

    def blocked_tick() -> None:
        started.set()
        assert release.wait(timeout=1.0)
        raise RuntimeError("older tick failed late")

    scheduler = ScheduleScheduler(blocked_tick, interval_seconds=0.5)
    worker = threading.Thread(target=scheduler.tick)
    worker.start()
    assert started.wait(timeout=1.0)

    clock = iter((20.0, 20.2, 21.1))
    monkeypatch.setattr(module.time, "monotonic", lambda: next(clock))
    with pytest.raises(
        RuntimeError, match="did not stop before the bounded shutdown deadline"
    ):
        scheduler.stop()

    stop_failure = scheduler.snapshot().last_failure
    assert stop_failure is not None
    release.set()
    worker.join(timeout=1.0)
    assert not worker.is_alive()

    snapshot = scheduler.snapshot()
    assert snapshot.ticks == 0
    assert snapshot.failures == 2
    assert snapshot.last_failure == stop_failure
    assert "older tick failed late" not in snapshot.last_failure.message


def test_stop_timeout_keeps_runtime_quiescing_and_fleet_open() -> None:
    from oh_goon.application import ApplicationRuntime
    from oh_goon.runtime.lifecycle import RuntimeLifecycle, RuntimeLifecycleState

    class SchedulerStub:
        def stop(self) -> None:
            raise RuntimeError(
                "schedule scheduler worker did not stop before the bounded shutdown deadline"
            )

    class RunsStub:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def quiesce(self) -> None:
            self.calls.append("quiesce")

        def cancel_active_for_shutdown(self) -> None:
            self.calls.append("cancel")

        def shutdown(self, *, wait: bool = True) -> None:
            self.calls.append(f"shutdown:{wait}")

    runtime = object.__new__(ApplicationRuntime)
    lifecycle = RuntimeLifecycle()
    runs = RunsStub()
    object.__setattr__(runtime, "_lifecycle", lifecycle)
    object.__setattr__(runtime, "_schedule_scheduler", SchedulerStub())
    object.__setattr__(runtime, "runs", runs)

    with pytest.raises(
        RuntimeError, match="did not stop before the bounded shutdown deadline"
    ):
        runtime.shutdown(wait_for_runs=False)

    assert lifecycle.state is RuntimeLifecycleState.QUIESCING
    assert runs.calls == []


def test_stop_closes_tick_admission_until_a_new_scheduler_generation_starts() -> None:
    calls = 0

    def tick() -> None:
        nonlocal calls
        calls += 1

    scheduler = ScheduleScheduler(tick, interval_seconds=60)
    scheduler.stop()
    scheduler.tick()
    assert calls == 0

    scheduler.start()
    try:
        scheduler.tick()
        assert calls == 1
    finally:
        scheduler.stop()


@pytest.mark.parametrize("mutation_kind", ["create", "advance"])
def test_runtime_cannot_close_while_scheduler_tick_retains_mutation_lease(
    mutation_kind: str, monkeypatch
) -> None:
    import threading

    import oh_goon.runtime.schedule_scheduler as module
    from oh_goon.application import ApplicationRuntime
    from oh_goon.runtime.lifecycle import RuntimeLifecycle, RuntimeLifecycleState

    lifecycle = RuntimeLifecycle()
    started = threading.Event()
    release = threading.Event()
    durable_mutations: list[tuple[str, RuntimeLifecycleState]] = []

    def tick() -> None:
        started.set()
        assert release.wait(timeout=1.0)
        durable_mutations.append((mutation_kind, lifecycle.state))

    scheduler = ScheduleScheduler(tick, interval_seconds=0.5)
    tick_thread = threading.Thread(target=scheduler.tick)
    tick_thread.start()
    assert started.wait(timeout=1.0)

    class RunsStub:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def quiesce(self) -> None:
            self.calls.append("quiesce")

        def cancel_active_for_shutdown(self) -> None:
            self.calls.append("cancel")

        def shutdown(self, *, wait: bool = True) -> None:
            self.calls.append(f"shutdown:{wait}")

    runtime = object.__new__(ApplicationRuntime)
    runs = RunsStub()
    object.__setattr__(runtime, "_lifecycle", lifecycle)
    object.__setattr__(runtime, "_schedule_scheduler", scheduler)
    object.__setattr__(runtime, "runs", runs)

    clock = iter((30.0, 30.2, 31.1))
    monkeypatch.setattr(module.time, "monotonic", lambda: next(clock))
    with pytest.raises(
        RuntimeError, match="did not stop before the bounded shutdown deadline"
    ):
        runtime.shutdown(wait_for_runs=False)

    assert lifecycle.state is RuntimeLifecycleState.QUIESCING
    assert runs.calls == []
    assert durable_mutations == []

    release.set()
    tick_thread.join(timeout=1.0)
    assert not tick_thread.is_alive()
    assert durable_mutations == [(mutation_kind, RuntimeLifecycleState.QUIESCING)]
    assert lifecycle.state is RuntimeLifecycleState.QUIESCING
