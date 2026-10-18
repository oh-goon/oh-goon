from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class LinuxProcessSample:
    cpu_seconds: float
    rss_bytes: int
    peak_rss_bytes: int


def read_linux_process_sample(pid: int) -> LinuxProcessSample:
    proc = Path("/proc") / str(pid)
    stat_text = (proc / "stat").read_text(encoding="utf-8")
    close = stat_text.rfind(")")
    if close < 0:
        raise RuntimeError(f"invalid /proc/{pid}/stat format")
    fields = stat_text[close + 2 :].split()
    if len(fields) < 13:
        raise RuntimeError(f"incomplete /proc/{pid}/stat")
    clock_ticks = int(os.sysconf("SC_CLK_TCK"))
    cpu_seconds = (int(fields[11]) + int(fields[12])) / clock_ticks

    values: dict[str, int] = {}
    for line in (proc / "status").read_text(encoding="utf-8").splitlines():
        if line.startswith(("VmRSS:", "VmHWM:")):
            name, value, unit = line.split()[:3]
            if unit != "kB":
                raise RuntimeError(f"unexpected {name} unit {unit!r}")
            values[name.rstrip(":")] = int(value) * 1024
    return LinuxProcessSample(
        cpu_seconds=cpu_seconds,
        rss_bytes=values.get("VmRSS", 0),
        peak_rss_bytes=values.get("VmHWM", 0),
    )
