from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from oh_goon import workflow as workflow_run


def test_owned_workflow_process_group_escalates_and_reaps_descendants(
    tmp_path: Path,
) -> None:
    if os.name == "nt":
        pytest.skip("POSIX process-group contract")
    marker = tmp_path / "grandchild.pid"
    grandchild_code = (
        "import os,signal,time; "
        "signal.signal(signal.SIGINT, signal.SIG_IGN); "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        f"open({str(marker)!r},'w').write(str(os.getpid())); "
        "time.sleep(30)"
    )
    parent_code = (
        "import subprocess,sys,time; "
        f"subprocess.Popen([sys.executable,'-c',{grandchild_code!r}]); "
        "time.sleep(30)"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", parent_code],
        start_new_session=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    deadline = time.monotonic() + 3
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert marker.exists()
    grandchild_pid = int(marker.read_text())

    workflow_run._terminate_owned_process_group(proc)

    assert proc.poll() is not None
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        stat_path = Path(f"/proc/{grandchild_pid}/stat")
        if not stat_path.exists():
            break
        fields = stat_path.read_text(encoding="utf-8").split()
        if len(fields) > 2 and fields[2] == "Z":
            break
        time.sleep(0.01)
    else:
        pytest.fail(
            "owned grandchild remained live after bounded cancellation escalation"
        )


def test_workflow_stream_launches_owned_command_in_separate_process_group(
    monkeypatch,
) -> None:
    observed: dict[str, object] = {}

    class FakeProcess:
        stdout = iter(())
        pid = 12345

        def wait(self, timeout=None):
            return 0

        def poll(self):
            return 0

    def fake_popen(*args, **kwargs):
        observed.update(kwargs)
        return FakeProcess()

    monkeypatch.setattr(workflow_run.subprocess, "Popen", fake_popen)
    assert workflow_run._stream(["probe"], env={}) == (0, "")
    if os.name != "nt":
        assert observed["start_new_session"] is True


def test_owned_workflow_group_outlives_leader_until_stubborn_descendant_is_reaped(
    tmp_path: Path,
) -> None:
    if os.name == "nt":
        pytest.skip("POSIX process-group contract")
    marker = tmp_path / "leader-exit-grandchild.pid"
    grandchild_code = (
        "import os,signal,time; "
        "signal.signal(signal.SIGINT, signal.SIG_IGN); "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        f"open({str(marker)!r},'w').write(str(os.getpid())); "
        "time.sleep(30)"
    )
    parent_code = (
        "import os,signal,subprocess,sys,time; "
        "signal.signal(signal.SIGINT, lambda *_: os._exit(130)); "
        f"subprocess.Popen([sys.executable,'-c',{grandchild_code!r}]); "
        "time.sleep(30)"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", parent_code],
        start_new_session=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    deadline = time.monotonic() + 3
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert marker.exists()
    grandchild_pid = int(marker.read_text())

    workflow_run._terminate_owned_process_group(proc)

    assert proc.poll() is not None
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        stat_path = Path(f"/proc/{grandchild_pid}/stat")
        if not stat_path.exists():
            break
        fields = stat_path.read_text(encoding="utf-8").split()
        if len(fields) > 2 and fields[2] == "Z":
            break
        time.sleep(0.01)
    else:
        pytest.fail(
            "stubborn descendant survived after its process-group leader exited"
        )
