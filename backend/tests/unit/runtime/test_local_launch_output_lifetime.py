from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from queue import Queue
from typing import Any

import pytest

from oh_goon.runtime import local_launch_supervisor as supervisor
from oh_goon.runtime.local_launch_plan import LocalLaunchPlan, LocalServicePlan

ROOT = Path(__file__).resolve().parents[4]
pytestmark = [
    pytest.mark.process,
    pytest.mark.skipif(os.name != "posix", reason="native POSIX launch process required"),
]


def _wait_for(predicate: Callable[[], bool]) -> None:
    deadline = time.monotonic() + 2
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert predicate()


def _plan(root: Path, command: tuple[str, ...]) -> LocalLaunchPlan:
    return LocalLaunchPlan(
        profile="contributor-dev",
        services=(LocalServicePlan("frontend", 1, command, root, "127.0.0.1", 1, "/", 200),),
    )


@pytest.mark.parametrize("invalid_output", [False, True], ids=["eof", "decode-error"])
def test_native_launch_output_closes_after_eof_or_read_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, invalid_output: bool
) -> None:
    release = tmp_path / "release"
    script = tmp_path / "service.py"
    script.write_text(
        "import os, pathlib, sys, time\n"
        "print('service-output', flush=True)\n"
        f"while not pathlib.Path({str(release)!r}).exists(): time.sleep(0.01)\n"
        + (
            "os.write(1, b'\\xff\\n')\n"
            if invalid_output
            else "print('final-output', flush=True)\n"
        ),
        encoding="utf-8",
    )
    errors: Queue[BaseException] = Queue()
    native_thread = threading.Thread

    def record_reader_error(
        *, target: Callable[..., None], kwargs: dict[str, Any], daemon: bool, name: str
    ) -> threading.Thread:
        def observe() -> None:
            try:
                target(**kwargs)
            except UnicodeError as error:
                errors.put(error)

        return native_thread(target=observe, daemon=daemon, name=name)

    monkeypatch.setattr(threading, "Thread", record_reader_error)
    running = supervisor.start_contributor_launch(
        root=ROOT,
        env={**os.environ, "OH_GOON_DB": str(tmp_path / "state" / "oh-goon.db")},
        plan=_plan(tmp_path, (sys.executable, str(script))),
        token="lifetime-test",
        probe=lambda _service: (True, "HTTP 200"),
    )
    process = running.processes[0]
    stream = process.stdout
    assert stream is not None
    try:
        _wait_for(lambda: "[frontend] service-output" in running.diagnostic_tail())
        release.touch()
        assert process.wait(timeout=5) == 0
        if invalid_output:
            assert isinstance(errors.get(timeout=2), UnicodeDecodeError)
        else:
            _wait_for(lambda: "[frontend] final-output" in running.diagnostic_tail())
            assert errors.empty()
        _wait_for(lambda: stream.closed)
    finally:
        running.stop()
        stream.close()
    assert (
        running.owner.store.manifest(running.owner.identity.launch_id).lifecycle.state.value
        == "stopped"
    )


def test_native_launch_stop_closes_output_with_launch_object_retained(tmp_path: Path) -> None:
    script = tmp_path / "service.py"
    script.write_text("import time\nprint('service-output', flush=True)\ntime.sleep(60)\n")
    running = supervisor.start_contributor_launch(
        root=ROOT,
        env={**os.environ, "OH_GOON_DB": str(tmp_path / "state" / "oh-goon.db")},
        plan=_plan(tmp_path, (sys.executable, str(script))),
        token="lifetime-test",
        probe=lambda _service: (True, "HTTP 200"),
    )
    stream = running.processes[0].stdout
    assert stream is not None
    try:
        _wait_for(lambda: "[frontend] service-output" in running.diagnostic_tail())
        running.stop()
        _wait_for(lambda: stream.closed)
        assert running.processes[0].poll() is not None
        assert (
            running.owner.store.manifest(running.owner.identity.launch_id).lifecycle.state.value
            == "stopped"
        )
    finally:
        if running.processes[0].poll() is None:
            running.stop()
        stream.close()


@pytest.mark.parametrize("reader_start_fails", [False, True], ids=["registration", "reader-start"])
def test_failed_launch_registration_closes_captured_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reader_start_fails: bool
) -> None:
    entrypoint = tmp_path / "backend/.venv/bin/oh-goon-local-launch-exec"
    entrypoint.parent.mkdir(parents=True)
    entrypoint.write_text("#!/bin/sh\nprintf 'bridge-output\\n'\nexit 7\n")
    entrypoint.chmod(0o700)
    native_popen = subprocess.Popen
    processes: list[subprocess.Popen[str]] = []

    def record_process(*args: Any, **kwargs: Any) -> subprocess.Popen[str]:
        process = native_popen(*args, **kwargs)
        processes.append(process)
        if reader_start_fails:
            assert process.wait(timeout=5) == 7
        return process

    monkeypatch.setattr(subprocess, "Popen", record_process)
    if reader_start_fails:

        def fail_start(_thread: threading.Thread) -> None:
            raise RuntimeError("reader start unavailable")

        monkeypatch.setattr(threading.Thread, "start", fail_start)
    try:
        with pytest.raises(
            RuntimeError if reader_start_fails else supervisor.LocalLaunchStartupError,
            match="reader start unavailable"
            if reader_start_fails
            else "before service.spawned registration",
        ):
            supervisor.start_contributor_launch(
                root=tmp_path,
                env={**os.environ, "OH_GOON_DB": str(tmp_path / "state" / "oh-goon.db")},
                plan=_plan(tmp_path, ("unused",)),
                token="lifetime-test",
            )
        assert len(processes) == 1
        process = processes[0]
        assert process.wait(timeout=5) == 7
        stream = process.stdout
        assert stream is not None
        _wait_for(lambda: stream.closed)
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            if process.stdout is not None:
                process.stdout.close()
