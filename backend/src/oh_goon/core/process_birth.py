from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# Native current and historical states: proc_pid_stat(5), Linux man-pages.
# https://man7.org/linux/man-pages/man5/proc_pid_stat.5.html
_LINUX_PROCESS_STATES = frozenset("RSDZTtWXxKPI")


@dataclass(frozen=True, slots=True)
class ProcessBirthIdentity:
    """Stable identity of one Linux process instance; PID alone is never identity."""

    pid: int
    boot_id: str
    start_ticks: int
    provider: str = "linux-procfs-v1"

    def __post_init__(self) -> None:
        if self.pid <= 0:
            raise ValueError("process birth identity pid must be positive")
        if not self.boot_id:
            raise ValueError("process birth identity boot_id is required")
        if self.start_ticks < 0:
            raise ValueError("process birth identity start_ticks must be >= 0")
        if self.provider != "linux-procfs-v1":
            raise ValueError("unsupported process birth identity provider")

    def to_payload(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "pid": self.pid,
            "boot_id": self.boot_id,
            "start_ticks": self.start_ticks,
        }

    @classmethod
    def from_payload(cls, payload: object) -> ProcessBirthIdentity:
        if not isinstance(payload, dict):
            raise ValueError("process birth identity payload must be an object")
        provider = payload.get("provider")
        pid = payload.get("pid")
        boot_id = payload.get("boot_id")
        start_ticks = payload.get("start_ticks")
        if not isinstance(provider, str):
            raise ValueError("process birth identity provider must be a string")
        if not isinstance(pid, int) or isinstance(pid, bool):
            raise ValueError("process birth identity pid must be an integer")
        if not isinstance(boot_id, str):
            raise ValueError("process birth identity boot_id must be a string")
        if not isinstance(start_ticks, int) or isinstance(start_ticks, bool):
            raise ValueError("process birth identity start_ticks must be an integer")
        return cls(pid=pid, boot_id=boot_id, start_ticks=start_ticks, provider=provider)


def observe_process_birth(
    pid: int | None = None, *, proc_root: Path = Path("/proc")
) -> ProcessBirthIdentity | None:
    """Observe Linux process birth identity without treating absence as lifecycle proof.

    Unsupported procfs, permission denial, or a process disappearing during observation
    returns None. Callers must preserve the last authoritative lifecycle state.
    """

    target_pid = os.getpid() if pid is None else pid
    if target_pid <= 0:
        raise ValueError("process pid must be positive")
    try:
        boot_id = (proc_root / "sys/kernel/random/boot_id").read_text(encoding="utf-8").strip()
        stat = (proc_root / str(target_pid) / "stat").read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return None
    observed = _parse_process_stat(stat, pid=target_pid)
    if not boot_id or observed is None:
        return None
    _, start_ticks = observed
    try:
        return ProcessBirthIdentity(pid=target_pid, boot_id=boot_id, start_ticks=start_ticks)
    except ValueError:
        return None


def process_birth_matches(
    expected: ProcessBirthIdentity, *, proc_root: Path = Path("/proc")
) -> bool:
    observed = observe_process_birth(expected.pid, proc_root=proc_root)
    return observed == expected


def observe_process_liveness(pid: int, *, proc_root: Path = Path("/proc")) -> bool | None:
    """Return executable liveness, distinguishing zombies from running processes.

    PID existence is not lifecycle authority on Linux: zombie/dead procfs entries can
    remain addressable and make ``kill(pid, 0)`` succeed even though no work can run.
    ``None`` means this host cannot prove liveness from procfs and callers may use a
    weaker platform fallback without confusing it with positive Linux evidence.
    """

    try:
        state = _read_process_state(pid, proc_root=proc_root)
    except FileNotFoundError:
        return False
    return None if state is None else state not in {"Z", "X", "x"}


def observe_process_state(pid: int, *, proc_root: Path = Path("/proc")) -> str | None:
    """Read one valid native state; missing or unreadable views remain unknown.

    Unlike the legacy liveness projection, None includes an absent stat record.
    Callers requiring kernel absence must establish that through their OS owner.
    A later birth observation cannot explain why an earlier stat read was absent.
    """
    try:
        return _read_process_state(pid, proc_root=proc_root)
    except FileNotFoundError:
        return None


def _read_process_state(pid: int, *, proc_root: Path) -> str | None:
    if pid <= 0:
        raise ValueError("process pid must be positive")
    if not proc_root.is_dir():
        return None
    try:
        stat = (proc_root / str(pid) / "stat").read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        raise
    except (OSError, UnicodeError):
        return None
    observed = _parse_process_stat(stat, pid=pid)
    return None if observed is None else observed[0]


def _parse_process_stat(record: str, *, pid: int) -> tuple[str, int] | None:
    """Validate the shared native header before interpreting positional fields."""
    # comm may itself contain parentheses. Its final ')' ends field 2; state
    # must remain field 3 or another numeric field could masquerade as birth.
    opening = record.find("(")
    close = record.rfind(")")
    if (
        opening <= 0
        or close < opening
        or record[:opening] != f"{pid} "
        or record[close + 1 : close + 2] != " "
    ):
        return None
    fields = record[close + 1 :].split()
    # starttime is field 22; fields[0] is field 3, therefore index 19.
    if len(fields) <= 19 or fields[0] not in _LINUX_PROCESS_STATES:
        return None
    try:
        start_ticks = int(fields[19])
    except ValueError:
        return None
    if start_ticks < 0:
        return None
    return fields[0], start_ticks
