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
    assert configured_cpu_budget({}) == 2
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
    assert default.worker_ceiling == 2
    assert disabled.worker_ceiling == 3
    assert disabled.affinity_cpu_count == 3
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
    assert budget.worker_ceiling == 2
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
    assert budget.worker_ceiling == 2
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
