from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from oh_goon.core.errors import FleetPlanInvalid

_CGROUP_V2_ROOT = Path("/sys/fs/cgroup")


@dataclass(frozen=True, slots=True)
class LocalExecutorCapacity:
    cpu_millis: int
    memory_bytes: int
    max_processes: int | None


def observe_local_executor_capacity(cgroup_root: Path | None) -> LocalExecutorCapacity:
    return LocalExecutorCapacity(
        cpu_millis=_observed_cpu_millis(cgroup_root),
        memory_bytes=_observed_memory_bytes(cgroup_root),
        max_processes=_observed_pid_limit(cgroup_root),
    )


def _observed_cpu_millis(cgroup_root: Path | None) -> int:
    observed = max(1, os.cpu_count() or 1) * 1000
    for path in _capacity_limit_paths("cpu.max", cgroup_root):
        try:
            quota, period = path.read_text(encoding="ascii").split()[:2]
            if quota != "max":
                observed = min(observed, max(1, int(int(quota) / int(period) * 1000)))
        except (OSError, ValueError, ZeroDivisionError):
            pass
    return observed


def _observed_memory_bytes(cgroup_root: Path | None) -> int:
    observed: int | None = None
    try:
        for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines():
            if line.startswith("MemTotal:"):
                observed = int(line.split()[1]) * 1024
                break
    except (OSError, ValueError):
        pass
    for path in _capacity_limit_paths("memory.max", cgroup_root):
        try:
            value = path.read_text(encoding="ascii").strip()
            if value != "max":
                limit = int(value)
                observed = limit if observed is None else min(observed, limit)
        except (OSError, ValueError):
            pass
    if observed is None:
        raise FleetPlanInvalid("Fleet cannot prove the local executor memory capacity")
    return observed


def _observed_pid_limit(cgroup_root: Path | None) -> int | None:
    observed: int | None = None
    for path in _capacity_limit_paths("pids.max", cgroup_root):
        try:
            value = path.read_text(encoding="ascii").strip()
            if value != "max":
                limit = int(value)
                observed = limit if observed is None else min(observed, limit)
        except (OSError, ValueError):
            pass
    return observed


def _capacity_limit_paths(name: str, cgroup_root: Path | None) -> tuple[Path, ...]:
    """Return every inherited cgroup-v2 limit that constrains ``cgroup_root``.

    CPU, memory and PID controllers are hierarchical. Looking only at the delegated
    root and filesystem root can overstate capacity when an intermediate systemd slice
    owns the effective limit. Local executor admission must therefore prove the whole
    ancestor chain rather than treating a child ``max`` value as physical capacity.
    """

    hierarchy = _CGROUP_V2_ROOT.resolve()
    paths: list[Path] = [hierarchy / name]
    if cgroup_root is None:
        return tuple(paths)

    candidate = cgroup_root.resolve()
    try:
        candidate.relative_to(hierarchy)
    except ValueError:
        external = candidate / name
        if external not in paths:
            paths.append(external)
        return tuple(paths)

    lineage: list[Path] = []
    current = candidate
    while current != hierarchy:
        lineage.append(current / name)
        current = current.parent
    for path in reversed(lineage):
        if path not in paths:
            paths.append(path)
    return tuple(paths)
