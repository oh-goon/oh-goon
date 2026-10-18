from __future__ import annotations

from pathlib import Path

import pytest

from oh_goon.api import dependencies
from oh_goon.application import ApplicationRuntime
from oh_goon.core.errors import ErrorKind, ExecutionBusy
from oh_goon.core.models.run import FleetRunFailure
from oh_goon.core.vocabulary import FleetRunFailureStage
from oh_goon.runtime.schedule_scheduler import ScheduleScheduler


def test_api_dependencies_share_one_application_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OH_GOON_DB", str(tmp_path / "runtime.db"))
    dependencies.application.cache_clear()
    runtime = dependencies.application()
    try:
        assert dependencies.fleet_service() is runtime.fleet
        assert dependencies.fleet_run_control_plane() is runtime.runs
        assert dependencies.schedule_service() is runtime.schedules
        assert not hasattr(runtime, "database")
        assert not hasattr(runtime, "state")
    finally:
        runtime.shutdown(wait_for_runs=False)
        dependencies.application.cache_clear()


def test_application_runtime_background_lifecycle_is_explicit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OH_GOON_DB", str(tmp_path / "runtime.db"))
    runtime = ApplicationRuntime.build()
    try:
        assert runtime._schedule_scheduler.snapshot().running is False
        runtime.start_background()
        assert runtime._schedule_scheduler.snapshot().running is True
    finally:
        runtime.shutdown(wait_for_runs=False)
    assert runtime._schedule_scheduler.snapshot().running is False



def test_fleet_run_failure_preserves_domain_semantics() -> None:
    failure = FleetRunFailure.from_exception(
        ExecutionBusy("another execution owns the lease"),
        stage=FleetRunFailureStage.SUBMISSION,
    )
    assert failure.code == "EXECUTION_BUSY"
    assert failure.kind is ErrorKind.CONFLICT
    assert failure.retryable is True
    assert failure.stage is FleetRunFailureStage.SUBMISSION


def test_fleet_run_failure_normalizes_unexpected_exception() -> None:
    failure = FleetRunFailure.from_exception(
        RuntimeError("boom"),
        stage=FleetRunFailureStage.EXECUTION,
    )
    assert failure.code == "UNEXPECTED_RUN_FAILURE"
    assert failure.kind is ErrorKind.INTERNAL
    assert failure.retryable is False
    assert failure.message == "boom"


def test_schedule_scheduler_records_failure_then_recovers() -> None:
    calls = 0

    def tick() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary scheduler failure")

    scheduler = ScheduleScheduler(tick, interval_seconds=60)
    scheduler.tick()
    failed = scheduler.snapshot()
    assert failed.ticks == 0
    assert failed.failures == 1
    assert failed.last_failure is not None
    assert failed.last_failure.code == "UNEXPECTED_BACKGROUND_FAILURE"
    assert failed.last_failure.message == "temporary scheduler failure"

    scheduler.tick()
    recovered = scheduler.snapshot()
    assert recovered.ticks == 1
    assert recovered.failures == 1
    assert recovered.last_failure is None


def test_schedule_scheduler_worker_is_managed_non_daemon() -> None:
    scheduler = ScheduleScheduler(lambda: None, interval_seconds=60)
    scheduler.start()
    try:
        assert scheduler._thread is not None  # unit-level lifecycle assertion
        assert scheduler._thread.daemon is False
    finally:
        scheduler.stop()
    assert scheduler.snapshot().running is False


def test_state_free_developer_cli_does_not_build_application_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from oh_goon import cli

    monkeypatch.setattr(
        cli.ApplicationRuntime,
        "build",
        staticmethod(
            lambda: pytest.fail("state-free developer command opened application runtime")
        ),
    )
    assert cli.main(["dev", "capability", "show", "update"]) == 0
