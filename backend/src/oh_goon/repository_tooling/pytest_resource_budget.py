"""Resolve initial pytest worker capacity from caller intent and native OS facts.

This is a worker ceiling for the existing batch executor, not a process-tree CPU
limiter or a live resource controller. Native cgroups retain quota authority.
"""

from __future__ import annotations

import math
import os
import sys
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TypedDict

MEMORY_HEADROOM_BYTES = 1024**3
ESTIMATED_WORKER_MEMORY_BYTES = 512 * 1024**2


def _small_text(path: Path) -> str | None:
    try:
        with path.open(encoding="utf-8") as stream:
            text = stream.read(8193)
    except (OSError, UnicodeError):
        return None
    return text.strip() if len(text) <= 8192 else None


def _nonnegative_integer(value: str | None) -> int | None:
    if value is None or not value.isascii() or not value.isdecimal():
        return None
    try:
        return int(value)
    except ValueError:
        return None


def configured_cpu_budget(environ: Mapping[str, str]) -> int:
    value = _nonnegative_integer(environ.get("OH_GOON_TEST_CPUS", "2"))
    if value is None or value > 4096:
        raise ValueError("OH_GOON_TEST_CPUS must be an integer between 0 and 4096")
    return value


@dataclass(frozen=True, slots=True)
class PytestCgroupObservation:
    path: str
    cpu_max: str | None
    cpu_capacity: float | None
    memory_max_bytes: int | None
    memory_current_bytes: int | None


@dataclass(frozen=True, slots=True)
class PytestCgroupUsageSnapshot:
    path: str
    pid_bound: bool
    cpu_stat: tuple[tuple[str, int], ...] | None
    memory_peak_bytes: int | None
    peak_tasks: int | None


def read_pytest_cgroup_usage(path: Path) -> PytestCgroupUsageSnapshot:
    processes = _small_text(path / "cgroup.procs")
    if processes is None or str(os.getpid()) not in processes.splitlines():
        return PytestCgroupUsageSnapshot(str(path), False, None, None, None)
    cpu_text = _small_text(path / "cpu.stat")
    counters: list[tuple[str, int]] = []
    for line in (cpu_text or "").splitlines():
        fields = line.split()
        if len(fields) == 2 and (value := _nonnegative_integer(fields[1])) is not None:
            counters.append((fields[0], value))
    return PytestCgroupUsageSnapshot(
        str(path),
        True,
        tuple(counters) if counters else None,
        _nonnegative_integer(_small_text(path / "memory.peak")),
        _nonnegative_integer(_small_text(path / "pids.peak")),
    )


def _cgroup_observations(
    proc_root: Path, cgroup_root: Path
) -> tuple[tuple[PytestCgroupObservation, ...], str, PytestCgroupUsageSnapshot | None]:
    membership = _small_text(proc_root / "self/cgroup")
    if membership is None:
        return (), "unavailable: native cgroup membership is unreadable", None
    unified = next((line[3:] for line in membership.splitlines() if line.startswith("0::")), None)
    if unified is None:
        return (), "unavailable: no unified cgroup membership", None
    relative = Path(unified.removeprefix("/"))
    if not unified.startswith("/") or ".." in relative.parts:
        return (), "unavailable: native cgroup membership is not a contained absolute path", None
    current = cgroup_root / relative
    usage = read_pytest_cgroup_usage(current)
    if not usage.pid_bound:
        return (), "unavailable: mapped cgroup does not establish the current PID", None
    observations: list[PytestCgroupObservation] = []
    while True:
        cpu_max = _small_text(current / "cpu.max")
        fields = cpu_max.split() if cpu_max else []
        capacity = None
        if len(fields) == 2:
            quota = _nonnegative_integer(fields[0])
            period = _nonnegative_integer(fields[1])
            if quota is not None and period is not None and quota > 0 and period > 0:
                capacity = quota / period
        observations.append(
            PytestCgroupObservation(
                str(current),
                cpu_max,
                capacity,
                _nonnegative_integer(_small_text(current / "memory.max")),
                _nonnegative_integer(_small_text(current / "memory.current")),
            )
        )
        if current == cgroup_root:
            return tuple(observations), "available: PID-bound unified cgroup ancestry", usage
        current = current.parent


def _available_memory(proc_root: Path) -> int | None:
    for line in (_small_text(proc_root / "meminfo") or "").splitlines():
        fields = line.split()
        if len(fields) == 3 and fields[0] == "MemAvailable:" and fields[2] == "kB":
            value = _nonnegative_integer(fields[1])
            return value * 1024 if value is not None else None
    return None


@dataclass(frozen=True, slots=True)
class PytestResourceBudget:
    requested_workers: int
    configured_cpus: int
    affinity_cpu_count: int | None
    native_cpu_count: int
    cgroup_cpu_capacity: float | None
    host_available_memory_bytes: int | None
    cgroup_remaining_memory_bytes: int | None
    available_memory_bytes: int | None
    memory_worker_capacity: int | None
    worker_ceiling: int
    cgroups: tuple[PytestCgroupObservation, ...]
    cgroup_observation_status: str
    cgroup_usage_started: PytestCgroupUsageSnapshot | None

    def execution_policy(self) -> dict[str, object]:
        return {
            "requested_workers": self.requested_workers,
            "configured_cpus": self.configured_cpus,
            "worker_ceiling": self.worker_ceiling,
            "affinity_cpu_count": self.affinity_cpu_count,
            "native_cpu_count": self.native_cpu_count,
            "cgroup_cpu_capacity": self.cgroup_cpu_capacity,
            "memory_worker_capacity": self.memory_worker_capacity,
            "memory_headroom_bytes": MEMORY_HEADROOM_BYTES,
            "estimated_worker_memory_bytes": ESTIMATED_WORKER_MEMORY_BYTES,
        }

    def report(self, *, effective_workers: int) -> dict[str, object]:
        payload = asdict(self)
        payload.pop("cgroup_usage_started")
        payload.update(
            {
                "effective_workers": effective_workers,
                "cpu_budget_role": "initial worker ceiling; not process-tree enforcement",
                "native_cgroup_cpu_quota_observed": self.cgroup_cpu_capacity is not None,
                "memory_guard": "initial capacity estimate; not a hard memory limit",
                "memory_headroom_bytes": MEMORY_HEADROOM_BYTES,
                "estimated_worker_memory_bytes": ESTIMATED_WORKER_MEMORY_BYTES,
            }
        )
        return payload


def resolve_pytest_resource_budget(
    requested_workers: int,
    *,
    environ: Mapping[str, str] | None = None,
    proc_root: Path = Path("/proc"),
    cgroup_root: Path = Path("/sys/fs/cgroup"),
) -> PytestResourceBudget:
    configured = configured_cpu_budget(os.environ if environ is None else environ)
    if requested_workers < 1:
        raise ValueError("requested pytest workers must be positive")
    try:
        affinity = len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        affinity = None
    if affinity == 0:
        raise ValueError("native CPU affinity allows no pytest execution")
    native_count = affinity if affinity is not None else (os.cpu_count() or 1)
    cgroups, cgroup_status, cgroup_usage = _cgroup_observations(proc_root, cgroup_root)
    quotas = [row.cpu_capacity for row in cgroups if row.cpu_capacity is not None]
    cgroup_capacity = min(quotas) if quotas else None
    cpu_capacity = (
        min(native_count, cgroup_capacity) if cgroup_capacity is not None else native_count
    )
    ceilings = [requested_workers, max(1, math.floor(cpu_capacity))]
    if configured:
        ceilings.append(configured)
    host_memory = _available_memory(proc_root)
    remaining = [
        max(0, row.memory_max_bytes - row.memory_current_bytes)
        for row in cgroups
        if row.memory_max_bytes is not None and row.memory_current_bytes is not None
    ]
    cgroup_memory = min(remaining) if remaining else None
    memories = [value for value in (host_memory, cgroup_memory) if value is not None]
    available = min(memories) if memories else None
    memory_capacity = None
    if available is not None:
        memory_capacity = max(
            1, (available - MEMORY_HEADROOM_BYTES) // ESTIMATED_WORKER_MEMORY_BYTES
        )
        ceilings.append(memory_capacity)
    return PytestResourceBudget(
        requested_workers,
        configured,
        affinity,
        native_count,
        cgroup_capacity,
        host_memory,
        cgroup_memory,
        available,
        memory_capacity,
        min(ceilings),
        cgroups,
        cgroup_status,
        cgroup_usage,
    )


def pytest_cgroup_diagnostics(
    before: PytestCgroupUsageSnapshot | None, after: PytestCgroupUsageSnapshot | None
) -> dict[str, object]:
    payload: dict[str, object] = {
        "path": before.path if before else None,
        "scope": "entire_observed_cgroup",
        "may_include_other_processes": True,
        "pid_bound": False,
        "availability": "unavailable: no PID-bound end observation of the resolved cgroup",
        "cpu_stat_deltas": None,
        "memory_peak_bytes": None,
        "baseline_memory_peak_bytes": None,
        "peak_tasks": None,
        "baseline_peak_tasks": None,
        "peak_scope": "cgroup_lifetime_high_water_marks",
        "peak_tasks_kind": "concurrent_tasks_and_threads",
        "counts_total_child_creations": False,
    }
    if before is None or after is None or not before.pid_bound or not after.pid_bound:
        return payload
    if before.path != after.path:
        return payload
    deltas: dict[str, int | None] | None = None
    if before.cpu_stat is not None and after.cpu_stat is not None:
        start = dict(before.cpu_stat)
        end = dict(after.cpu_stat)
        keys = set(start) | set(end) | {"nr_periods", "nr_throttled", "throttled_usec"}
        deltas = {
            key: end[key] - start[key]
            if key in start and key in end and end[key] >= start[key]
            else None
            for key in sorted(keys)
        }
    payload.update(
        {
            "availability": "available: current PID remains in the resolved cgroup",
            "pid_bound": True,
            "cpu_stat_deltas": deltas,
            "memory_peak_bytes": after.memory_peak_bytes,
            "baseline_memory_peak_bytes": before.memory_peak_bytes,
            "peak_tasks": after.peak_tasks,
            "baseline_peak_tasks": before.peak_tasks,
        }
    )
    return payload


@dataclass(frozen=True, slots=True)
class PytestUsageSnapshot:
    self_user_seconds: float
    self_system_seconds: float
    waited_children_user_seconds: float
    waited_children_system_seconds: float
    peak_waited_child_rss_bytes: int


class PytestCpuTimes(TypedDict):
    self_user_seconds: float
    self_system_seconds: float
    waited_children_user_seconds: float
    waited_children_system_seconds: float
    total_seconds: float


class PytestUsageDiagnostics(TypedDict):
    cpu_seconds: PytestCpuTimes
    peak_waited_child_rss_bytes: int
    baseline_peak_waited_child_rss_bytes: int
    peak_rss_scope: str


def read_pytest_usage() -> PytestUsageSnapshot | None:
    try:
        import resource
    except ImportError:
        return None
    try:
        own = resource.getrusage(resource.RUSAGE_SELF)
        children = resource.getrusage(resource.RUSAGE_CHILDREN)
    except (OSError, ValueError):
        return None
    rss_multiplier = 1 if sys.platform == "darwin" else 1024
    return PytestUsageSnapshot(
        own.ru_utime,
        own.ru_stime,
        children.ru_utime,
        children.ru_stime,
        int(children.ru_maxrss) * rss_multiplier,
    )


def pytest_usage_diagnostics(
    before: PytestUsageSnapshot | None, after: PytestUsageSnapshot | None
) -> PytestUsageDiagnostics | None:
    if before is None or after is None:
        return None
    cpu: PytestCpuTimes = {
        "self_user_seconds": max(0.0, after.self_user_seconds - before.self_user_seconds),
        "self_system_seconds": max(0.0, after.self_system_seconds - before.self_system_seconds),
        "waited_children_user_seconds": max(
            0.0, after.waited_children_user_seconds - before.waited_children_user_seconds
        ),
        "waited_children_system_seconds": max(
            0.0, after.waited_children_system_seconds - before.waited_children_system_seconds
        ),
        "total_seconds": 0.0,
    }
    cpu["total_seconds"] = (
        cpu["self_user_seconds"]
        + cpu["self_system_seconds"]
        + cpu["waited_children_user_seconds"]
        + cpu["waited_children_system_seconds"]
    )
    return {
        "cpu_seconds": cpu,
        "peak_waited_child_rss_bytes": after.peak_waited_child_rss_bytes,
        "baseline_peak_waited_child_rss_bytes": before.peak_waited_child_rss_bytes,
        "peak_rss_scope": "waited-child process-lifetime high-water mark; not aggregate peak RSS",
    }
