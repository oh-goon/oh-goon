"""Resolve initial pytest worker capacity from caller intent and native OS facts.

This is a worker ceiling for the existing batch executor, not a process-tree CPU
limiter or a live resource controller. Native cgroups retain quota authority.
"""

from __future__ import annotations

import math
import os
import stat
import sys
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal, TypedDict

MEMORY_HEADROOM_BYTES = 1024**3
ESTIMATED_WORKER_MEMORY_BYTES = 512 * 1024**2


def _small_text(path: Path, *, dir_fd: int | None = None) -> str | None:
    try:
        if dir_fd is None:
            with path.open(encoding="utf-8") as stream:
                text = stream.read(8193)
        else:
            descriptor = os.open(
                path,
                os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=dir_fd,
            )
            try:
                with os.fdopen(descriptor, encoding="utf-8", closefd=False) as stream:
                    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                        return None
                    text = stream.read(8193)
            finally:
                os.close(descriptor)
    except (OSError, UnicodeError, NotImplementedError):
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
    value = _nonnegative_integer(environ.get("OH_GOON_TEST_CPUS", "0"))
    if value is None or value > 4096:
        raise ValueError("OH_GOON_TEST_CPUS must be an integer between 0 and 4096")
    return value


def _metric_key(value: str) -> bool:
    return (
        bool(value)
        and value.isascii()
        and all(character.isalnum() or character in "_." for character in value)
    )


def _counters(text: str | None) -> tuple[tuple[str, int], ...] | None:
    if not text:
        return None
    counters: dict[str, int] = {}
    for line in text.splitlines():
        fields = line.split()
        if (
            len(fields) != 2
            or not _metric_key(fields[0])
            or fields[0] in counters
            or (value := _nonnegative_integer(fields[1])) is None
        ):
            return None
        counters[fields[0]] = value
    return tuple(sorted(counters.items()))


def _io_stat(
    text: str | None,
) -> tuple[tuple[str, tuple[tuple[str, int | str], ...]], ...] | None:
    if text is None:
        return None
    devices: dict[str, tuple[tuple[str, int | str], ...]] = {}
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 2:
            return None
        device = fields[0]
        parts = device.split(":")
        if (
            len(parts) != 2
            or any(
                (number := _nonnegative_integer(part)) is None or str(number) != part
                for part in parts
            )
            or device in devices
        ):
            return None
        counters: dict[str, int | str] = {}
        for field in fields[1:]:
            key, separator, raw = field.partition("=")
            if not separator or not _metric_key(key) or key in counters:
                return None
            value = _nonnegative_integer(raw)
            if value is not None:
                counters[key] = value
            elif (
                key == "cost.vrate"
                and len(parts := raw.split(".")) == 2
                and all(_nonnegative_integer(part) is not None for part in parts)
            ):
                # Native iocost emits this decimal gauge; preserve its spelling,
                # without floating conversion or treating it as a counter.
                counters[key] = raw
            else:
                return None
        devices[device] = tuple(sorted(counters.items()))
    return tuple(sorted(devices.items()))


def _pressure_totals(text: str | None) -> tuple[tuple[str, int], ...] | None:
    if not text:
        return None
    totals: dict[str, int] = {}
    for line in text.splitlines():
        fields = line.split()
        if not fields or fields[0] not in {"some", "full"} or fields[0] in totals:
            return None
        values: dict[str, str] = {}
        for field in fields[1:]:
            key, separator, raw = field.partition("=")
            if not separator or key in values:
                return None
            values[key] = raw
        if set(values) != {"avg10", "avg60", "avg300", "total"}:
            return None
        total = _nonnegative_integer(values["total"])
        if total is None:
            return None
        for key in ("avg10", "avg60", "avg300"):
            raw = values[key]
            parts = raw.split(".")
            if len(parts) > 2 or any(_nonnegative_integer(part) is None for part in parts):
                return None
            if not 0 <= float(raw) <= 100:
                return None
        totals[fields[0]] = total
    return tuple(sorted(totals.items())) if set(totals) == {"some", "full"} else None


def _limit(value: str | None) -> int | Literal["max"] | None:
    return "max" if value == "max" else _nonnegative_integer(value)


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
    directory_identity: tuple[int, int] | None = None
    observed_pid: int | None = None
    memory_current_bytes: int | None = None
    memory_high_bytes: int | Literal["max"] | None = None
    memory_max_bytes: int | Literal["max"] | None = None
    swap_current_bytes: int | None = None
    swap_peak_bytes: int | None = None
    swap_high_bytes: int | Literal["max"] | None = None
    swap_max_bytes: int | Literal["max"] | None = None
    current_tasks: int | None = None
    task_limit: int | Literal["max"] | None = None
    memory_events: tuple[tuple[str, int], ...] | None = None
    memory_events_local: tuple[tuple[str, int], ...] | None = None
    swap_events: tuple[tuple[str, int], ...] | None = None
    pids_events: tuple[tuple[str, int], ...] | None = None
    io_stat: tuple[tuple[str, tuple[tuple[str, int | str], ...]], ...] | None = None
    memory_pressure_usec: tuple[tuple[str, int], ...] | None = None
    io_pressure_usec: tuple[tuple[str, int], ...] | None = None


def read_pytest_cgroup_usage(path: Path) -> PytestCgroupUsageSnapshot:
    unavailable = PytestCgroupUsageSnapshot(str(path), False, None, None, None)
    if not all(hasattr(os, name) for name in ("O_DIRECTORY", "O_CLOEXEC", "O_NOFOLLOW")):
        return unavailable
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW)
    except (OSError, NotImplementedError):
        return unavailable
    try:
        opened = os.fstat(descriptor)
        identity = opened.st_dev, opened.st_ino
        pid = os.getpid()

        def text(name: str) -> str | None:
            return _small_text(Path(name), dir_fd=descriptor)

        def member() -> bool:
            processes = text("cgroup.procs")
            return (
                processes is not None
                and str(pid) in processes.splitlines()
                and all(
                    (value := _nonnegative_integer(member)) is not None and value > 0
                    for member in processes.splitlines()
                )
            )

        if not member():
            return unavailable
        snapshot = PytestCgroupUsageSnapshot(
            str(path),
            True,
            _counters(text("cpu.stat")),
            _nonnegative_integer(text("memory.peak")),
            _nonnegative_integer(text("pids.peak")),
            directory_identity=identity,
            observed_pid=pid,
            memory_current_bytes=_nonnegative_integer(text("memory.current")),
            memory_high_bytes=_limit(text("memory.high")),
            memory_max_bytes=_limit(text("memory.max")),
            swap_current_bytes=_nonnegative_integer(text("memory.swap.current")),
            swap_peak_bytes=_nonnegative_integer(text("memory.swap.peak")),
            swap_high_bytes=_limit(text("memory.swap.high")),
            swap_max_bytes=_limit(text("memory.swap.max")),
            current_tasks=_nonnegative_integer(text("pids.current")),
            task_limit=_limit(text("pids.max")),
            memory_events=_counters(text("memory.events")),
            memory_events_local=_counters(text("memory.events.local")),
            swap_events=_counters(text("memory.swap.events")),
            pids_events=_counters(text("pids.events")),
            io_stat=_io_stat(text("io.stat")),
            memory_pressure_usec=_pressure_totals(text("memory.pressure")),
            io_pressure_usec=_pressure_totals(text("io.pressure")),
        )
        if not member() or os.getpid() != pid:
            return unavailable
        current = path.lstat()
        if not stat.S_ISDIR(current.st_mode) or (current.st_dev, current.st_ino) != identity:
            return unavailable
        return snapshot
    except (OSError, NotImplementedError):
        return unavailable
    finally:
        os.close(descriptor)


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
    if not unified.startswith("/") or relative.is_absolute() or ".." in relative.parts:
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


def _counter_deltas(
    before: tuple[tuple[str, int], ...] | None,
    after: tuple[tuple[str, int], ...] | None,
    *,
    required: frozenset[str] = frozenset(),
) -> dict[str, int | None] | None:
    if before is None or after is None:
        return None
    start, end = dict(before), dict(after)
    if len(start) != len(before) or len(end) != len(after):
        return None
    return {
        key: end[key] - start[key]
        if key in start and key in end and end[key] >= start[key]
        else None
        for key in sorted(set(start) | set(end) | required)
    }


def _io_deltas(
    before: tuple[tuple[str, tuple[tuple[str, int | str], ...]], ...] | None,
    after: tuple[tuple[str, tuple[tuple[str, int | str], ...]], ...] | None,
) -> dict[str, dict[str, int | None] | None] | None:
    if before is None or after is None:
        return None
    start, end = dict(before), dict(after)
    cumulative = frozenset({"rbytes", "wbytes", "rios", "wios", "dbytes", "dios"})
    return {
        device: _counter_deltas(
            tuple(
                (key, value)
                for key, value in start[device]
                if key in cumulative and isinstance(value, int)
            )
            if device in start
            else None,
            tuple(
                (key, value)
                for key, value in end[device]
                if key in cumulative and isinstance(value, int)
            )
            if device in end
            else None,
            required=cumulative,
        )
        for device in sorted(set(start) | set(end))
    }


def _lifetime_peak(before: int | None, after: int | None) -> int | None:
    return None if before is not None and after is not None and after < before else after


def pytest_cgroup_diagnostics(
    before: PytestCgroupUsageSnapshot | None, after: PytestCgroupUsageSnapshot | None
) -> dict[str, object]:
    payload: dict[str, object] = {
        "path": before.path if before else None,
        "scope": "entire_observed_cgroup",
        "may_include_other_processes": True,
        "pid_bound": False,
        "availability": "unavailable: no PID-bound end observation of the resolved cgroup",
        "directory_identity": None,
        "observed_pid": None,
        "cpu_stat_deltas": None,
        "memory_peak_bytes": None,
        "baseline_memory_peak_bytes": None,
        "peak_tasks": None,
        "baseline_peak_tasks": None,
        "peak_scope": "cgroup_lifetime_high_water_marks",
        "peak_tasks_kind": "concurrent_tasks_and_threads",
        "counts_total_child_creations": False,
        "memory_events_deltas": None,
        "memory_events_local_deltas": None,
        "swap_events_deltas": None,
        "pids_events_deltas": None,
        "io_stat": None,
        "baseline_io_stat": None,
        "io_stat_deltas": None,
        "memory_pressure_total_usec_deltas": None,
        "io_pressure_total_usec_deltas": None,
        "pressure_averages_measured": False,
        "event_scope": "native cgroup counters; hierarchy/mount options affect propagation",
        "memory_events_local_scope": "observed cgroup only",
        "oom_attribution": "scope observation only; no per-test or memory.max cause inferred",
        "io_stat_delta_fields": ("rbytes", "wbytes", "rios", "wios", "dbytes", "dios"),
    }
    gauges = (
        "memory_current_bytes",
        "memory_high_bytes",
        "memory_max_bytes",
        "swap_current_bytes",
        "swap_high_bytes",
        "swap_max_bytes",
        "current_tasks",
        "task_limit",
    )
    for name in (*gauges, "swap_peak_bytes"):
        payload[name] = None
        payload["baseline_" + name] = None
    if before is None or after is None or not before.pid_bound or not after.pid_bound:
        return payload
    if (
        before.path != after.path
        or before.directory_identity is None
        or before.directory_identity != after.directory_identity
        or before.observed_pid is None
        or before.observed_pid != after.observed_pid
        or before.observed_pid != os.getpid()
    ):
        payload["availability"] = "unavailable: cgroup directory/observer continuity is unproven"
        return payload
    payload.update(
        {
            "availability": "available: same cgroup directory and observer PID at both samples",
            "pid_bound": True,
            "observed_pid": before.observed_pid,
            "directory_identity": {
                "device": before.directory_identity[0],
                "inode": before.directory_identity[1],
            },
            "cpu_stat_deltas": _counter_deltas(
                before.cpu_stat,
                after.cpu_stat,
                required=frozenset({"nr_periods", "nr_throttled", "throttled_usec"}),
            ),
            "memory_peak_bytes": _lifetime_peak(before.memory_peak_bytes, after.memory_peak_bytes),
            "baseline_memory_peak_bytes": before.memory_peak_bytes,
            "peak_tasks": _lifetime_peak(before.peak_tasks, after.peak_tasks),
            "baseline_peak_tasks": before.peak_tasks,
            "swap_peak_bytes": _lifetime_peak(before.swap_peak_bytes, after.swap_peak_bytes),
            "baseline_swap_peak_bytes": before.swap_peak_bytes,
            "memory_events_deltas": _counter_deltas(before.memory_events, after.memory_events),
            "memory_events_local_deltas": _counter_deltas(
                before.memory_events_local, after.memory_events_local
            ),
            "swap_events_deltas": _counter_deltas(before.swap_events, after.swap_events),
            "pids_events_deltas": _counter_deltas(before.pids_events, after.pids_events),
            "io_stat": {device: dict(values) for device, values in after.io_stat}
            if after.io_stat is not None
            else None,
            "baseline_io_stat": {device: dict(values) for device, values in before.io_stat}
            if before.io_stat is not None
            else None,
            "io_stat_deltas": _io_deltas(before.io_stat, after.io_stat),
            "memory_pressure_total_usec_deltas": _counter_deltas(
                before.memory_pressure_usec, after.memory_pressure_usec
            ),
            "io_pressure_total_usec_deltas": _counter_deltas(
                before.io_pressure_usec, after.io_pressure_usec
            ),
        }
    )
    for name in gauges:
        payload[name] = getattr(after, name)
        payload["baseline_" + name] = getattr(before, name)
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
