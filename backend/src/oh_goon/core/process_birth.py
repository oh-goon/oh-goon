from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


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
    if not boot_id:
        return None
    # /proc/<pid>/stat field 2 is `(comm)` and may contain spaces/parentheses.
    # The final ')' terminates comm; the remaining tokens start at field 3.
    close = stat.rfind(")")
    if close < 0 or close + 2 > len(stat):
        return None
    head = stat[:close]
    open_paren = head.find("(")
    if open_paren <= 0:
        return None
    try:
        stat_pid = int(stat[:open_paren].strip())
    except ValueError:
        return None
    tail = stat[close + 1 :].strip().split()
    # starttime is field 22. tail[0] is field 3, therefore index 19.
    if len(tail) <= 19:
        return None
    try:
        start_ticks = int(tail[19])
    except ValueError:
        return None
    if stat_pid != target_pid:
        return None
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
    close = stat.rfind(")")
    opening = stat.find("(")
    if opening <= 0 or close < opening or close + 2 > len(stat):
        return None
    tail = stat[close + 1 :].strip().split()
    if len(tail) <= 19 or len(tail[0]) != 1:
        return None
    try:
        if int(stat[:opening].strip()) != pid or int(tail[19]) < 0:
            return None
    except ValueError:
        return None
    return tail[0]
