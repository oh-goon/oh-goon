from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import cast

import pytest

from oh_goon.repository_tooling import pytest_batch_orchestration as orchestration
from oh_goon.repository_tooling.pytest_resource_budget import (
    PytestUsageSnapshot,
    configured_cpu_budget,
    pytest_cgroup_diagnostics,
    pytest_usage_diagnostics,
    read_pytest_cgroup_usage,
    resolve_pytest_resource_budget,
)


@pytest.fixture
def native_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    proc = tmp_path / "proc"
    (proc / "self").mkdir(parents=True)
    (proc / "self/cgroup").write_text("0::/user.slice/tests.scope\n", encoding="utf-8")
    (proc / "meminfo").write_text("MemAvailable: 16777216 kB\n", encoding="utf-8")
    cgroup = tmp_path / "cgroup"
    (cgroup / "user.slice/tests.scope").mkdir(parents=True)
    (cgroup / "user.slice/tests.scope/cgroup.procs").write_text(
        str(os.getpid()) + "\n", encoding="utf-8"
    )
    for directory in (cgroup, cgroup / "user.slice", cgroup / "user.slice/tests.scope"):
        (directory / "cpu.max").write_text("max 100000\n", encoding="utf-8")
        (directory / "memory.max").write_text("max\n", encoding="utf-8")
        (directory / "memory.current").write_text("0\n", encoding="utf-8")
    monkeypatch.setattr(
        os, "sched_getaffinity", lambda _pid: set(range(16)), raising=False
    )
    return proc, cgroup


@pytest.mark.parametrize("value", ["", "-1", "2.5", "abc", "4097", " 2", "+2"])
def test_invalid_cpu_configuration_fails_before_native_collection(
    value: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("OH_GOON_TEST_CPUS", value)

    def forbidden_collection(*args: object, **kwargs: object) -> None:
        raise AssertionError("invalid resource configuration reached native collection")

    monkeypatch.setattr(orchestration, "collect_native_pytest", forbidden_collection)
    with pytest.raises(SystemExit) as failure:
        orchestration.run_batch_cli(
            argparse.ArgumentParser(),
            argparse.Namespace(workers=4),
            cast(orchestration.BatchCallbacks, None),
        )
    assert failure.value.code == 2
    assert (
        "OH_GOON_TEST_CPUS must be an integer between 0 and 4096"
        in capsys.readouterr().err
    )


def test_cpu_configuration_defaults_and_explicit_limits() -> None:
    assert configured_cpu_budget({}) == 0
    assert configured_cpu_budget({"OH_GOON_TEST_CPUS": "0"}) == 0
    assert configured_cpu_budget({"OH_GOON_TEST_CPUS": "4096"}) == 4096


def test_disabled_configured_ceiling_still_respects_native_affinity(
    native_files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    proc, cgroup = native_files
    monkeypatch.setattr(os, "sched_getaffinity", lambda _pid: {2, 5, 8}, raising=False)
    default = resolve_pytest_resource_budget(
        8, environ={}, proc_root=proc, cgroup_root=cgroup
    )
    disabled = resolve_pytest_resource_budget(
        8, environ={"OH_GOON_TEST_CPUS": "0"}, proc_root=proc, cgroup_root=cgroup
    )
    assert default.worker_ceiling == 3
    assert disabled.worker_ceiling == 3
    assert disabled.affinity_cpu_count == 3
    limited = resolve_pytest_resource_budget(
        8, environ={"OH_GOON_TEST_CPUS": "2"}, proc_root=proc, cgroup_root=cgroup
    )
    assert limited.worker_ceiling == 2
    assert (
        disabled.report(effective_workers=3)["native_cgroup_cpu_quota_observed"]
        is False
    )


def test_serial_execution_lane_is_never_expanded(
    native_files: tuple[Path, Path],
) -> None:
    proc, cgroup = native_files
    budget = resolve_pytest_resource_budget(
        1, environ={"OH_GOON_TEST_CPUS": "4096"}, proc_root=proc, cgroup_root=cgroup
    )
    assert budget.requested_workers == 1
    assert budget.worker_ceiling == 1


def test_configured_budget_binds_execution_provenance_even_for_serial_lanes(
    native_files: tuple[Path, Path],
) -> None:
    from oh_goon.repository_tooling.test_batch_receipts import execution_plan_identity

    proc, cgroup = native_files
    identities: list[str] = []
    for configured in (1, 2):
        budget = resolve_pytest_resource_budget(
            1,
            environ={"OH_GOON_TEST_CPUS": str(configured)},
            proc_root=proc,
            cgroup_root=cgroup,
        )
        assert budget.worker_ceiling == 1
        identities.append(
            execution_plan_identity(
                suite_identity="sha256:same-correctness-suite",
                batch_size=1,
                batch_timeout=10,
                timeout_bisection=True,
                workers=1,
                suite_timeout=60,
                environment_retries=0,
                selected_batch=None,
                scheduled_members=("test_probe.py",),
                reused_members=(),
                resource_budget=budget.execution_policy(),
            )
        )
    assert identities[0] != identities[1]


def test_all_native_cgroup_ancestors_constrain_initial_workers(
    native_files: tuple[Path, Path],
) -> None:
    proc, cgroup = native_files
    (cgroup / "cpu.max").write_text("250000 100000\n", encoding="utf-8")
    (cgroup / "user.slice/cpu.max").write_text("150000 100000\n", encoding="utf-8")
    (cgroup / "user.slice/tests.scope/cpu.max").write_text(
        "400000 100000\n", encoding="utf-8"
    )
    budget = resolve_pytest_resource_budget(
        8, environ={"OH_GOON_TEST_CPUS": "8"}, proc_root=proc, cgroup_root=cgroup
    )
    assert budget.cgroup_cpu_capacity == 1.5
    assert budget.worker_ceiling == 1
    assert [row.cpu_capacity for row in budget.cgroups] == [4.0, 1.5, 2.5]
    assert (
        budget.report(effective_workers=1)["native_cgroup_cpu_quota_observed"] is True
    )


def test_fractional_native_cpu_quota_keeps_one_execution_worker(
    native_files: tuple[Path, Path],
) -> None:
    proc, cgroup = native_files
    (cgroup / "cpu.max").write_text("25000 100000\n", encoding="utf-8")
    budget = resolve_pytest_resource_budget(
        8, environ={"OH_GOON_TEST_CPUS": "0"}, proc_root=proc, cgroup_root=cgroup
    )
    assert budget.cgroup_cpu_capacity == 0.25
    assert budget.worker_ceiling == 1


def test_ancestor_remaining_memory_limits_workers_without_claiming_hard_limit(
    native_files: tuple[Path, Path],
) -> None:
    proc, cgroup = native_files
    (cgroup / "user.slice/memory.max").write_text(str(4 * 1024**3), encoding="utf-8")
    (cgroup / "user.slice/memory.current").write_text(
        str(2 * 1024**3), encoding="utf-8"
    )
    budget = resolve_pytest_resource_budget(
        8, environ={"OH_GOON_TEST_CPUS": "0"}, proc_root=proc, cgroup_root=cgroup
    )
    assert budget.available_memory_bytes == 2 * 1024**3
    assert budget.memory_worker_capacity == 2
    assert budget.worker_ceiling == 2
    assert budget.report(effective_workers=2)["memory_guard"] == (
        "initial capacity estimate; not a hard memory limit"
    )


def test_initial_memory_observation_keeps_one_worker_and_is_not_reread(
    native_files: tuple[Path, Path],
) -> None:
    proc, cgroup = native_files
    (proc / "meminfo").write_text("MemAvailable: 524288 kB\n", encoding="utf-8")
    environment = {"OH_GOON_TEST_CPUS": "8"}
    budget = resolve_pytest_resource_budget(
        8, environ=environment, proc_root=proc, cgroup_root=cgroup
    )
    environment["OH_GOON_TEST_CPUS"] = "1"
    (proc / "meminfo").write_text("MemAvailable: 16777216 kB\n", encoding="utf-8")
    assert budget.configured_cpus == 8
    assert budget.memory_worker_capacity == 1
    assert budget.worker_ceiling == 1
    assert budget.available_memory_bytes == 512 * 1024**2


def test_missing_native_memory_and_cgroup_observations_are_explicit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        os, "sched_getaffinity", lambda _pid: {0, 1, 2, 3}, raising=False
    )
    budget = resolve_pytest_resource_budget(
        8, environ={}, proc_root=tmp_path / "proc", cgroup_root=tmp_path / "cgroup"
    )
    assert budget.worker_ceiling == 4
    assert budget.cgroups == ()
    assert budget.available_memory_bytes is None
    assert budget.memory_worker_capacity is None


def test_unproven_cgroup_path_does_not_supply_other_process_limits(
    native_files: tuple[Path, Path],
) -> None:
    proc, cgroup = native_files
    leaf = cgroup / "user.slice/tests.scope"
    (leaf / "cpu.max").write_text("50000 100000\n", encoding="utf-8")
    (leaf / "cgroup.procs").write_text(str(os.getpid() + 1) + "\n", encoding="utf-8")
    budget = resolve_pytest_resource_budget(
        8, environ={}, proc_root=proc, cgroup_root=cgroup
    )
    assert budget.worker_ceiling == 8
    assert budget.cgroups == ()
    assert budget.cgroup_cpu_capacity is None
    assert budget.cgroup_observation_status == (
        "unavailable: mapped cgroup does not establish the current PID"
    )


def test_whole_run_usage_includes_waited_children_without_summing_peak_rss() -> None:
    before = PytestUsageSnapshot(1.0, 2.0, 3.0, 4.0, 1000)
    after = PytestUsageSnapshot(2.0, 4.0, 7.0, 12.0, 5000)
    usage = pytest_usage_diagnostics(before, after)
    assert usage == {
        "cpu_seconds": {
            "self_user_seconds": 1.0,
            "self_system_seconds": 2.0,
            "waited_children_user_seconds": 4.0,
            "waited_children_system_seconds": 8.0,
            "total_seconds": 15.0,
        },
        "peak_waited_child_rss_bytes": 5000,
        "baseline_peak_waited_child_rss_bytes": 1000,
        "peak_rss_scope": "waited-child process-lifetime high-water mark; not aggregate peak RSS",
    }
    assert pytest_usage_diagnostics(None, after) is None


def test_observed_cgroup_reports_counter_deltas_and_lifetime_peaks(
    native_files: tuple[Path, Path],
) -> None:
    proc, cgroup = native_files
    leaf = cgroup / "user.slice/tests.scope"
    (leaf / "cpu.stat").write_text(
        "usage_usec 1000\nnr_periods 50\nnr_throttled 2\nthrottled_usec 100\n",
        encoding="utf-8",
    )
    (leaf / "memory.peak").write_text("10000000\n", encoding="utf-8")
    (leaf / "pids.peak").write_text("4\n", encoding="utf-8")
    budget = resolve_pytest_resource_budget(
        4, environ={}, proc_root=proc, cgroup_root=cgroup
    )
    (leaf / "cpu.stat").write_text(
        "usage_usec 3000\nnr_periods 60\nnr_throttled 5\nthrottled_usec 900\n",
        encoding="utf-8",
    )
    (leaf / "memory.peak").write_text("20000000\n", encoding="utf-8")
    (leaf / "pids.peak").write_text("9\n", encoding="utf-8")

    observed = pytest_cgroup_diagnostics(
        budget.cgroup_usage_started, read_pytest_cgroup_usage(leaf)
    )
    assert observed["cpu_stat_deltas"] == {
        "usage_usec": 2000,
        "nr_periods": 10,
        "nr_throttled": 3,
        "throttled_usec": 800,
    }
    assert observed["memory_peak_bytes"] == 20000000
    assert observed["baseline_memory_peak_bytes"] == 10000000
    assert observed["peak_tasks"] == 9
    assert observed["baseline_peak_tasks"] == 4
    assert observed["scope"] == "entire_observed_cgroup"
    assert observed["may_include_other_processes"] is True
    assert observed["peak_tasks_kind"] == "concurrent_tasks_and_threads"
    assert observed["counts_total_child_creations"] is False


def test_cgroup_end_observation_rejects_pid_mismatch(
    native_files: tuple[Path, Path],
) -> None:
    proc, cgroup = native_files
    leaf = cgroup / "user.slice/tests.scope"
    budget = resolve_pytest_resource_budget(
        4, environ={}, proc_root=proc, cgroup_root=cgroup
    )
    (leaf / "cgroup.procs").write_text(str(os.getpid() + 1) + "\n", encoding="utf-8")
    (leaf / "cpu.stat").write_text("throttled_usec 100\n", encoding="utf-8")
    observed = pytest_cgroup_diagnostics(
        budget.cgroup_usage_started, read_pytest_cgroup_usage(leaf)
    )
    assert observed["pid_bound"] is False
    assert observed["cpu_stat_deltas"] is None
    assert observed["memory_peak_bytes"] is None
    assert observed["peak_tasks"] is None


def test_missing_cgroup_statistics_remain_unknown(
    native_files: tuple[Path, Path],
) -> None:
    proc, cgroup = native_files
    leaf = cgroup / "user.slice/tests.scope"
    budget = resolve_pytest_resource_budget(
        4, environ={}, proc_root=proc, cgroup_root=cgroup
    )
    observed = pytest_cgroup_diagnostics(
        budget.cgroup_usage_started, read_pytest_cgroup_usage(leaf)
    )
    assert observed["pid_bound"] is True
    assert observed["cpu_stat_deltas"] is None
    assert observed["memory_peak_bytes"] is None
    assert observed["peak_tasks"] is None
    assert observed["scope"] == "entire_observed_cgroup"


def _telemetry(leaf: Path, fields: dict[str, str]) -> None:
    for name, value in fields.items():
        (leaf / name).write_text(value, encoding="utf-8")


def _pressure(some: int, full: int, average: str = "0.00") -> str:
    return (
        f"some avg10={average} avg60=0.00 avg300=0.00 total={some}\n"
        f"full avg10=0.00 avg60=0.00 avg300=0.00 total={full}\n"
    )


def test_native_pressure_metrics_keep_limits_gauges_totals_and_peaks_distinct(
    native_files: tuple[Path, Path],
) -> None:
    _proc, cgroup = native_files
    leaf = cgroup / "user.slice/tests.scope"
    _telemetry(
        leaf,
        {
            "memory.current": "400\n",
            "memory.high": "max\n",
            "memory.max": "1024\n",
            "memory.peak": "800\n",
            "memory.swap.current": "0\n",
            "memory.swap.high": "max\n",
            "memory.swap.max": "max\n",
            "memory.swap.peak": "100\n",
            "pids.current": "4\n",
            "pids.max": "max\n",
            "pids.peak": "7\n",
            "memory.events": "oom 2\noom_kill 1\nhigh 0\n",
            "memory.events.local": "oom 0\noom_kill 0\nhigh 0\n",
            "memory.swap.events": "high 0\nmax 1\nfail 2\n",
            "pids.events": "max 0\n",
            "memory.pressure": _pressure(100, 20),
            "io.pressure": _pressure(50, 10),
            "io.stat": "8:0 rbytes=100 wbytes=200 rios=1 wios=2 dbytes=0 dios=0 depth=4 missed=8 total=10\n",
        },
    )
    before = read_pytest_cgroup_usage(leaf)
    _telemetry(
        leaf,
        {
            "memory.current": "200\n",
            "memory.high": "512\n",
            "memory.max": "max\n",
            "memory.peak": "900\n",
            "memory.swap.current": "10\n",
            "memory.swap.high": "0\n",
            "memory.swap.max": "0\n",
            "memory.swap.peak": "110\n",
            "pids.current": "3\n",
            "pids.max": "16\n",
            "pids.peak": "8\n",
            "memory.events": "high 3\noom_kill 2\noom 3\n",
            "memory.events.local": "oom 0\noom_kill 0\nhigh 0\n",
            "memory.swap.events": "high 0\nmax 2\nfail 4\n",
            "pids.events": "max 1\n",
            "memory.pressure": _pressure(180, 30, "1.50"),
            "io.pressure": _pressure(50, 12),
            "io.stat": "8:0 wbytes=250 rbytes=180 rios=3 wios=4 dbytes=0 dios=0 depth=1 missed=2 total=3\n",
        },
    )
    observed = pytest_cgroup_diagnostics(before, read_pytest_cgroup_usage(leaf))
    assert observed["pid_bound"] is True
    assert observed["directory_identity"] == {
        "device": leaf.stat().st_dev,
        "inode": leaf.stat().st_ino,
    }
    assert (
        observed["baseline_memory_current_bytes"],
        observed["memory_current_bytes"],
    ) == (400, 200)
    assert (observed["baseline_memory_high_bytes"], observed["memory_high_bytes"]) == (
        "max",
        512,
    )
    assert (observed["baseline_memory_max_bytes"], observed["memory_max_bytes"]) == (
        1024,
        "max",
    )
    assert (
        observed["swap_current_bytes"],
        observed["swap_max_bytes"],
        observed["swap_high_bytes"],
    ) == (10, 0, 0)
    assert observed["memory_peak_bytes"] == 900 and observed["swap_peak_bytes"] == 110
    assert (
        observed["current_tasks"] == 3
        and observed["task_limit"] == 16
        and observed["peak_tasks"] == 8
    )
    assert observed["memory_events_deltas"] == {"oom": 1, "oom_kill": 1, "high": 3}
    assert observed["memory_events_local_deltas"] == {
        "oom": 0,
        "oom_kill": 0,
        "high": 0,
    }
    assert observed["swap_events_deltas"] == {"high": 0, "max": 1, "fail": 2}
    assert observed["pids_events_deltas"] == {"max": 1}
    assert observed["memory_pressure_total_usec_deltas"] == {"some": 80, "full": 10}
    assert observed["io_pressure_total_usec_deltas"] == {"some": 0, "full": 2}
    assert observed["pressure_averages_measured"] is False
    assert observed["io_stat_deltas"] == {
        "8:0": {
            "rbytes": 80,
            "wbytes": 50,
            "rios": 2,
            "wios": 2,
            "dbytes": 0,
            "dios": 0,
        }
    }
    assert observed["io_stat"] == {
        "8:0": {
            "rbytes": 180,
            "wbytes": 250,
            "rios": 3,
            "wios": 4,
            "dbytes": 0,
            "dios": 0,
            "depth": 1,
            "missed": 2,
            "total": 3,
        }
    }
    assert observed["may_include_other_processes"] is True
    assert "no per-test" in str(observed["oom_attribution"])


def test_native_iocost_decimal_gauge_preserves_cumulative_io(
    native_files: tuple[Path, Path],
) -> None:
    _proc, cgroup = native_files
    leaf = cgroup / "user.slice/tests.scope"
    _telemetry(
        leaf,
        {
            "io.stat": "8:0 rbytes=100 wbytes=200 rios=1 wios=2 dbytes=0 dios=0 cost.vrate=100.00\n"
        },
    )
    before = read_pytest_cgroup_usage(leaf)
    _telemetry(
        leaf,
        {
            "io.stat": "8:0 rbytes=180 wbytes=250 rios=3 wios=4 dbytes=0 dios=0 cost.vrate=55.50\n"
        },
    )
    observed = pytest_cgroup_diagnostics(before, read_pytest_cgroup_usage(leaf))
    assert observed["baseline_io_stat"] == {
        "8:0": {
            "rbytes": 100,
            "wbytes": 200,
            "rios": 1,
            "wios": 2,
            "dbytes": 0,
            "dios": 0,
            "cost.vrate": "100.00",
        }
    }
    assert observed["io_stat"] == {
        "8:0": {
            "rbytes": 180,
            "wbytes": 250,
            "rios": 3,
            "wios": 4,
            "dbytes": 0,
            "dios": 0,
            "cost.vrate": "55.50",
        }
    }
    assert observed["io_stat_deltas"] == {
        "8:0": {
            "rbytes": 80,
            "wbytes": 50,
            "rios": 2,
            "wios": 2,
            "dbytes": 0,
            "dios": 0,
        }
    }


def test_missing_limits_and_io_are_not_zero_or_unlimited(
    native_files: tuple[Path, Path],
) -> None:
    _proc, cgroup = native_files
    leaf = cgroup / "user.slice/tests.scope"
    _telemetry(leaf, {"memory.swap.current": "0\n", "memory.high": "max\n"})
    before = read_pytest_cgroup_usage(leaf)
    observed = pytest_cgroup_diagnostics(before, read_pytest_cgroup_usage(leaf))
    assert observed["memory_high_bytes"] == "max"
    assert observed["swap_current_bytes"] == 0
    assert observed["swap_max_bytes"] is None
    assert observed["io_stat"] is observed["io_stat_deltas"] is None
    assert observed["memory_events_deltas"] is None
    (leaf / "io.stat").write_text("", encoding="utf-8")
    empty = read_pytest_cgroup_usage(leaf)
    assert (
        pytest_cgroup_diagnostics(empty, read_pytest_cgroup_usage(leaf))[
            "io_stat_deltas"
        ]
        == {}
    )


@pytest.mark.parametrize(
    ("name", "contents", "attribute"),
    [
        ("cpu.stat", "usage_usec 1\nusage_usec 2\n", "cpu_stat"),
        ("cpu.stat", "usage_usec 1\nbroken\n", "cpu_stat"),
        ("memory.events", "oom 1\noom 2\n", "memory_events"),
        ("memory.events.local", "oom -1\n", "memory_events_local"),
        ("memory.swap.events", "max 0\ninvalid\n", "swap_events"),
        ("pids.events", "max 0\n" + "x" * 8193, "pids_events"),
        ("memory.high", "unlimited\n", "memory_high_bytes"),
        ("memory.swap.max", "-1\n", "swap_max_bytes"),
        ("io.stat", "8:0 rbytes=0 rbytes=1\n", "io_stat"),
        ("io.stat", "8:0 rbytes=0\n8:0 wbytes=1\n", "io_stat"),
        ("io.stat", "8:0 rbytes=0 wbytes=bad\n", "io_stat"),
        ("io.stat", "8:0 rbytes=1.00 cost.vrate=100.00\n", "io_stat"),
        ("io.stat", "8:0 rbytes=0 cost.vrate=bad\n", "io_stat"),
        ("io.stat", "08:0 rbytes=0\n", "io_stat"),
        (
            "memory.pressure",
            _pressure(0, 0) + "full avg10=0 avg60=0 avg300=0 total=0\n",
            "memory_pressure_usec",
        ),
        (
            "memory.pressure",
            _pressure(0, 0).replace("avg10=0.00", "avg10=nan"),
            "memory_pressure_usec",
        ),
        (
            "io.pressure",
            _pressure(0, 0).replace("total=0", "total=-1"),
            "io_pressure_usec",
        ),
        (
            "io.pressure",
            _pressure(0, 0).replace("total=0", "total=0 total=1"),
            "io_pressure_usec",
        ),
        ("io.pressure", "some avg10=0 avg60=0 total=0\n", "io_pressure_usec"),
    ],
)
def test_malformed_native_metrics_remain_unavailable(
    native_files: tuple[Path, Path], name: str, contents: str, attribute: str
) -> None:
    _proc, cgroup = native_files
    leaf = cgroup / "user.slice/tests.scope"
    _telemetry(leaf, {name: contents})
    snapshot = read_pytest_cgroup_usage(leaf)
    assert snapshot.pid_bound is True
    assert getattr(snapshot, attribute) is None
    assert snapshot.memory_current_bytes == 0


def test_resets_missing_counters_and_new_devices_do_not_invent_zero_deltas(
    native_files: tuple[Path, Path],
) -> None:
    _proc, cgroup = native_files
    leaf = cgroup / "user.slice/tests.scope"
    _telemetry(
        leaf,
        {
            "cpu.stat": "usage_usec 100\n",
            "memory.events": "oom 3\noom_kill 2\n",
            "memory.peak": "1000\n",
            "memory.swap.peak": "100\n",
            "pids.peak": "10\n",
            "memory.pressure": _pressure(100, 20),
            "io.stat": "8:0 rbytes=100 wbytes=200\n",
        },
    )
    before = read_pytest_cgroup_usage(leaf)
    _telemetry(
        leaf,
        {
            "cpu.stat": "usage_usec 1\n",
            "memory.events": "oom 1\nnew 0\n",
            "memory.peak": "500\n",
            "memory.swap.peak": "50\n",
            "pids.peak": "5\n",
            "memory.pressure": _pressure(10, 30),
            "io.stat": "8:0 rbytes=90 wbytes=250\n8:16 rbytes=0\n",
        },
    )
    observed = pytest_cgroup_diagnostics(before, read_pytest_cgroup_usage(leaf))
    assert observed["cpu_stat_deltas"] == {
        "usage_usec": None,
        "nr_periods": None,
        "nr_throttled": None,
        "throttled_usec": None,
    }
    assert observed["memory_events_deltas"] == {
        "oom": None,
        "oom_kill": None,
        "new": None,
    }
    assert observed["memory_pressure_total_usec_deltas"] == {"some": None, "full": 10}
    assert (
        observed["memory_peak_bytes"]
        is observed["swap_peak_bytes"]
        is observed["peak_tasks"]
        is None
    )
    assert observed["io_stat_deltas"] == {
        "8:0": {
            "rbytes": None,
            "wbytes": 50,
            "rios": None,
            "wios": None,
            "dbytes": None,
            "dios": None,
        },
        "8:16": None,
    }


def test_same_path_cgroup_replacement_cannot_supply_counter_continuity(
    native_files: tuple[Path, Path],
) -> None:
    _proc, cgroup = native_files
    leaf = cgroup / "user.slice/tests.scope"
    _telemetry(leaf, {"cpu.stat": "usage_usec 10\n"})
    before = read_pytest_cgroup_usage(leaf)
    leaf.rename(leaf.with_name("retired.scope"))
    leaf.mkdir()
    _telemetry(
        leaf, {"cgroup.procs": str(os.getpid()) + "\n", "cpu.stat": "usage_usec 20\n"}
    )
    observed = pytest_cgroup_diagnostics(before, read_pytest_cgroup_usage(leaf))
    assert observed["pid_bound"] is False
    assert observed["cpu_stat_deltas"] is None
    assert observed["memory_events_deltas"] is None
    assert "continuity" in str(observed["availability"])


def test_cgroup_samples_from_another_observer_cannot_claim_pid_continuity(
    native_files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _proc, cgroup = native_files
    leaf = cgroup / "user.slice/tests.scope"
    before = read_pytest_cgroup_usage(leaf)
    previous = os.getpid()
    monkeypatch.setattr(os, "getpid", lambda: previous + 1)
    _telemetry(leaf, {"cgroup.procs": str(previous + 1) + "\n"})
    after = read_pytest_cgroup_usage(leaf)
    assert before.pid_bound is after.pid_bound is True
    observed = pytest_cgroup_diagnostics(before, after)
    assert observed["pid_bound"] is False and observed["memory_current_bytes"] is None


@pytest.mark.parametrize("change", ["directory", "pid", "final-membership"])
def test_native_sample_refuses_changes_during_metric_reads(
    native_files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    from typing import Any

    _proc, cgroup = native_files
    leaf = cgroup / "user.slice/tests.scope"
    _telemetry(leaf, {"cpu.stat": "usage_usec 10\n"})
    native_open = os.open
    changed = []
    memberships = []

    def open_file(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        descriptor = native_open(path, flags, *args, **kwargs)
        if Path(path).name == "cgroup.procs" and kwargs.get("dir_fd") is not None:
            memberships.append(path)
        if kwargs.get("dir_fd") is not None and (
            (Path(path).name == "cpu.stat" and change != "final-membership")
            or (
                Path(path).name == "cgroup.procs"
                and len(memberships) == 2
                and change == "final-membership"
            )
        ):
            changed.append(change)
            if change in {"directory", "final-membership"}:
                leaf.rename(leaf.with_name("retired.scope"))
                leaf.mkdir()
                _telemetry(leaf, {"cgroup.procs": str(os.getpid()) + "\n"})
            else:
                _telemetry(leaf, {"cgroup.procs": str(os.getpid() + 1) + "\n"})
        return descriptor

    monkeypatch.setattr(os, "open", open_file)
    snapshot = read_pytest_cgroup_usage(leaf)
    assert changed == [change]
    assert snapshot.pid_bound is False and snapshot.cpu_stat is None


@pytest.mark.parametrize("membership", ["//outside", "///outside", "/../outside"])
def test_uncontained_membership_is_rejected_before_any_leaf_read(
    native_files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, membership: str
) -> None:
    from oh_goon.repository_tooling import pytest_resource_budget as owner

    proc, cgroup = native_files
    (proc / "self/cgroup").write_text(f"0::{membership}\n", encoding="utf-8")

    def forbidden_read(_path: Path) -> None:
        raise AssertionError("uncontained cgroup membership reached a leaf read")

    monkeypatch.setattr(owner, "read_pytest_cgroup_usage", forbidden_read)
    budget = resolve_pytest_resource_budget(
        2, environ={}, proc_root=proc, cgroup_root=cgroup
    )
    assert budget.cgroups == () and budget.cgroup_cpu_capacity is None
    assert "not a contained absolute path" in budget.cgroup_observation_status
