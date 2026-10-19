from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from oh_goon.core.models.repository import (
    IncludedRepositoryCatalogEntry,
    RepositoryCatalogEntry,
    RepositoryIdentity,
)
from oh_goon.core.vocabulary import FleetScope
from oh_goon.services.fleet_target_scheduler import FleetTargetScheduler


class _Session:
    @contextmanager
    def execution_connection_scope(self):
        yield None


class _Effects:
    def __init__(self) -> None:
        import threading

        self._second_failed = threading.Event()

    def execute_fleet_target(
        self,
        _intent,
        _capability,
        entry,
        _session,
        _isolation_backend=None,
        *,
        repository_admission=None,
    ):
        if entry.repository.name == "first":
            assert self._second_failed.wait(timeout=1)
            raise RuntimeError("first failure")
        if entry.repository.name == "second":
            self._second_failed.set()
            raise RuntimeError("second failure")


def _entry(tmp_path: Path, name: str) -> RepositoryCatalogEntry:
    return IncludedRepositoryCatalogEntry(
        scope=FleetScope.INCLUDED, repository=RepositoryIdentity(name=name, path=tmp_path / name)
    )


def test_parallel_failure_selection_is_deterministic_by_target_order(tmp_path: Path) -> None:
    scheduler = FleetTargetScheduler(_Effects())  # type: ignore[arg-type]
    targets = [_entry(tmp_path, "first"), _entry(tmp_path, "second")]

    with pytest.raises(RuntimeError, match="first failure"):
        scheduler.execute(
            SimpleNamespace(),  # type: ignore[arg-type]
            SimpleNamespace(),  # type: ignore[arg-type]
            targets,
            _Session(),  # type: ignore[arg-type]
            parallel_safe=True,
            repository_admissions=(),
        )


def test_parallel_scheduler_honors_resolved_max_parallel(tmp_path: Path) -> None:
    import threading

    state = {"active": 0, "max_active": 0}
    lock = threading.Lock()
    first_pair = threading.Barrier(2)
    calls = {"count": 0}

    class Effects:
        def execute_fleet_target(
            self,
            _intent,
            _capability,
            _entry,
            _session,
            _isolation_backend=None,
            *,
            repository_admission=None,
        ):
            with lock:
                calls["count"] += 1
                call_number = calls["count"]
                state["active"] += 1
                state["max_active"] = max(state["max_active"], state["active"])
            if call_number <= 2:
                first_pair.wait(timeout=1)
            with lock:
                state["active"] -= 1

    scheduler = FleetTargetScheduler(Effects())  # type: ignore[arg-type]
    targets = [_entry(tmp_path, f"repo-{index}") for index in range(6)]
    scheduler.execute(
        SimpleNamespace(),  # type: ignore[arg-type]
        SimpleNamespace(),  # type: ignore[arg-type]
        targets,
        _Session(),  # type: ignore[arg-type]
        parallel_safe=True,
        repository_admissions=(),
        max_parallel=2,
    )
    assert state["max_active"] == 2


def test_scheduler_forwards_connection_bound_authority_check(tmp_path: Path) -> None:
    observed: list[object] = []
    marker = object()

    class Effects:
        def execute_fleet_target(
            self,
            _intent,
            _capability,
            _entry,
            _session,
            _isolation_backend=None,
            *,
            repository_admission=None,
            validate_authority_in=None,
        ):
            observed.append(validate_authority_in)

    scheduler = FleetTargetScheduler(Effects())  # type: ignore[arg-type]
    scheduler.execute(
        SimpleNamespace(),  # type: ignore[arg-type]
        SimpleNamespace(),  # type: ignore[arg-type]
        [_entry(tmp_path, "repo")],
        _Session(),  # type: ignore[arg-type]
        parallel_safe=False,
        repository_admissions=(),
        validate_authority_in=marker,  # type: ignore[arg-type]
    )
    assert observed == [marker]


def test_parallel_failure_stops_unstarted_targets(tmp_path: Path) -> None:
    import threading

    second_started = threading.Event()
    observed: list[str] = []
    lock = threading.Lock()

    class Effects:
        def execute_fleet_target(
            self,
            _intent,
            _capability,
            entry,
            _session,
            _isolation_backend=None,
            *,
            repository_admission=None,
        ):
            with lock:
                observed.append(entry.repository.name)
            if entry.repository.name == "first":
                assert second_started.wait(timeout=1)
                raise RuntimeError("first failure")
            if entry.repository.name == "second":
                second_started.set()

    scheduler = FleetTargetScheduler(Effects())  # type: ignore[arg-type]
    targets = [_entry(tmp_path, name) for name in ("first", "second", "third", "fourth")]

    with pytest.raises(RuntimeError, match="first failure"):
        scheduler.execute(
            SimpleNamespace(),  # type: ignore[arg-type]
            SimpleNamespace(),  # type: ignore[arg-type]
            targets,
            _Session(),  # type: ignore[arg-type]
            parallel_safe=True,
            repository_admissions=(),
            max_parallel=2,
        )

    assert set(observed) == {"first", "second"}
    assert len(observed) == 2


def test_scheduler_reports_target_worker_and_connection_authority_boundaries(
    tmp_path: Path,
) -> None:
    activity: list[str] = []

    class Effects:
        def execute_fleet_target(
            self,
            _intent,
            _capability,
            _entry,
            _session,
            _isolation_backend=None,
            *,
            repository_admission=None,
            activity=None,
        ):
            assert activity is not None

    scheduler = FleetTargetScheduler(Effects())  # type: ignore[arg-type]
    scheduler.execute(
        SimpleNamespace(),  # type: ignore[arg-type]
        SimpleNamespace(),  # type: ignore[arg-type]
        [_entry(tmp_path, "repo")],
        _Session(),  # type: ignore[arg-type]
        parallel_safe=False,
        repository_admissions=(),
        activity=activity.append,
    )

    assert activity == [
        "Repository target worker entered for repo",
        "Repository execution authority ready for repo",
    ]
