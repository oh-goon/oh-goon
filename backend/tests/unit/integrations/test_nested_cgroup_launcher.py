from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from oh_goon.integrations import _fleet_plan_cgroup_resources as cgroup
from oh_goon.integrations import _fleet_plan_nested_cgroup_launcher as launcher


class _FakeProcess:
    pid = 4321

    def __init__(self, command, *, pass_fds):
        self.command = tuple(command)
        self.pass_fds = tuple(pass_fds)
        info_fd = int(self.command[self.command.index("--info-fd") + 1])
        block_fd = int(self.command[self.command.index("--block-fd") + 1])
        info_copy = os.dup(info_fd)
        self.block_copy = os.dup(block_fd)
        os.write(info_copy, (json.dumps({"child-pid": 9876}) + "\n").encode())
        os.close(info_copy)
        self.terminated = False

    def wait(self):
        os.close(self.block_copy)
        return 0

    def terminate(self):
        self.terminated = True


def test_nested_launcher_empties_delegated_root_before_releasing_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cgroup, "require_independent_native_parent_custody", lambda **_kw: None)
    nested = tmp_path / "nested-execution"
    monitor = tmp_path / "nested-monitor"
    nested.mkdir()
    monitor.mkdir()
    moves: list[tuple[Path, int]] = []
    observed: dict[str, _FakeProcess] = {}

    def fake_popen(command, *, pass_fds):
        process = _FakeProcess(command, pass_fds=pass_fds)
        observed["process"] = process
        return process

    rc = launcher.launch_nested_bubblewrap(
        nested,
        monitor,
        ("/usr/bin/bwrap", "--unshare-cgroup", "--", "make", "check"),
        nested_identity=(nested.stat().st_dev, nested.stat().st_ino),
        monitor_identity=(monitor.stat().st_dev, monitor.stat().st_ino),
        popen=fake_popen,
        move_process=lambda root, pid, *, identity: moves.append((root, pid)),
    )

    assert rc == 0
    process = observed["process"]
    assert "--as-pid-1" in process.command
    assert "--block-fd" in process.command
    assert "--info-fd" in process.command
    assert moves == [
        (nested.resolve(), os.getpid()),
        (monitor.resolve(), os.getpid()),
        (monitor.resolve(), process.pid),
    ]
