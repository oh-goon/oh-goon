"""Real SQLite disk pressure preserves claimed Run uncertainty and recovery."""

from __future__ import annotations

import errno
import json
import os
import select
import shlex
import shutil
import signal
import sqlite3
import stat
import subprocess
import sys
import tempfile
from contextlib import closing
from pathlib import Path
from time import monotonic
from typing import Any

import pytest
import yaml

from oh_goon.application import ApplicationRuntime
from oh_goon.core.errors import StateConflict
from oh_goon.core.file_digest import sha256_file_with_size
from oh_goon.core.models.fleet_plan_evidence import PresentPlanArtifact
from oh_goon.core.models.run import FailedFleetRun
from oh_goon.core.models.run_events import ProcessStartedEvent
from oh_goon.core.path_layouts import ApplicationStatePathLayout
from oh_goon.core.process_birth import observe_process_birth
from oh_goon.core.product_identity import repository_id
from oh_goon.core.sqlite_journal_vocabulary import SQLITE_JOURNAL_MODE_ENV
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.execution import ExecutionCoordinator
from oh_goon.runtime.execution.lease_environment import SystemLeaseEnvironment

MIB = 1024 * 1024
pytestmark = [
    pytest.mark.process,
    pytest.mark.no_resume,
    pytest.mark.skipif(
        sys.platform != "linux"
        or any(shutil.which(tool) is None for tool in ("uv", "unshare", "mount", "strace")),
        reason="SQLite storage pressure requires native Linux namespaces, mount, strace and uv",
    ),
]

_APPLICATION = """import json
import os
from pathlib import Path
import sys

def main():
    root = Path(sys.argv[1])
    with (root / 'applications-started').open('a') as stream:
        stream.write('started\\n')
    if (root / 'fault-enabled').exists():
        with (root / 'ready').open('w') as stream:
            stream.write(json.dumps({'pid': os.getpid(), 'cwd': str(Path.cwd())}))
        with (root / 'hold').open('rb') as stream:
            stream.read(1)
    Path('model.bin').write_bytes(b'healthy retained output\\n')
    with (root / 'applications-finished').open('a') as stream:
        stream.write('finished\\n')
    print('application completed', flush=True)
    return 0
"""


def _package(root: Path) -> Path:
    from tests.factories.python_goon_projects import write_isolated_build_project

    repository = root / "repositories/target"
    repository.mkdir(parents=True)
    (repository / "subject.txt").write_text("SQLite pressure subject\n")
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    package = root / "workflow/goons/general/isolated-build"
    write_isolated_build_project(package)
    (package / "isolated_app.py").write_text(_APPLICATION)
    (package / "goon.yml").write_text(
        yaml.safe_dump(
            {
                "runtime": "python",
                "uv": {"lock_mode": "frozen"},
                "steps": [
                    {
                        "id": "produce",
                        "run": "isolated-build " + shlex.quote(str(root)),
                        "artifacts": [{"path": "model.bin", "retention": "content_store"}],
                    }
                ],
            },
            sort_keys=False,
        )
    )
    plan = root / "workflow/plan.yml"
    plan.write_text("goons:\n- import: general/isolated-build\n  from: goons\n")
    for name in ("ready", "hold"):
        os.mkfifo(root / name)
    return plan


def _environment(root: Path, journal_mode: str) -> dict[str, str]:
    environment = dict(os.environ)
    environment.pop("OH_GOON_EXECUTORS_FILE", None)
    environment.update(
        OH_GOON_DB=str(root / "database/runtime.sqlite"),
        OH_GOON_LEAGUE_FILE=str(root / "config/league.yml"),
        XDG_CONFIG_HOME=str(root / "config"),
        XDG_DATA_HOME=str(root / "data"),
        XDG_STATE_HOME=str(root / "state"),
        XDG_CACHE_HOME=str(root / "cache"),
        UV_CACHE_DIR=str(root / "uv-cache"),
        UV_PYTHON=sys.executable,
        UV_PYTHON_DOWNLOADS="never",
    )
    environment[SQLITE_JOURNAL_MODE_ENV] = journal_mode
    for name in ("database", "config", "data", "state", "cache", "uv-cache", "healthy"):
        (root / name).mkdir(mode=0o700)
    return environment


def _cli(root: Path, *arguments: str) -> dict[str, Any]:
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        completed = subprocess.run(
            [str(Path(sys.executable).with_name("oh-goon")), *arguments, "--json"],
            cwd=root,
            stdout=output,
            stderr=errors,
            stdin=subprocess.DEVNULL,
            timeout=30,
        )
        output.seek(0)
        errors.seek(0)
        body, diagnostic = output.read(256 * 1024 + 1), errors.read(64 * 1024 + 1)
    assert len(body) <= 256 * 1024 and len(diagnostic) <= 64 * 1024
    assert completed.returncode == 0, (arguments, body[-8192:], diagnostic[-8192:])
    value = json.loads(body)
    assert isinstance(value, dict)
    return value


def _fill_mount(mount: Path) -> int:
    descriptor = os.open(mount / "owned-filler", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    written = 0
    try:
        while written <= 17 * MIB:
            try:
                written += os.write(descriptor, b"x" * 65536)
            except OSError as error:
                assert error.errno == errno.ENOSPC
                assert os.statvfs(mount).f_bavail == 0
                return written
        raise AssertionError("dedicated SQLite mount did not return native ENOSPC")
    finally:
        os.close(descriptor)


def _witness(root: Path, journal_mode: str) -> int:
    database_path = Path(os.environ["OH_GOON_DB"])
    layout = ApplicationStatePathLayout.from_database(database_path)
    mount = layout.root
    subprocess.run(
        [
            "mount",
            "-t",
            "tmpfs",
            "-o",
            "size=16m,nr_inodes=512,mode=0700,nodev,nosuid,noexec",
            "tmpfs",
            str(mount),
        ],
        check=True,
    )
    bindings: list[Path] = []
    runtime: ApplicationRuntime | None = None
    descriptors: list[int] = []
    ready_fd: int | None = None
    tracer: subprocess.Popen[bytes] | None = None
    trace_errors = (root / "trace.stderr").open("wb")
    try:
        metadata, capacity = mount.stat(), os.statvfs(mount)
        assert metadata.st_uid == os.getuid() and stat.S_IMODE(metadata.st_mode) == 0o700
        assert metadata.st_dev != root.stat().st_dev
        assert capacity.f_blocks * capacity.f_frsize == 16 * MIB
        for target in (
            layout.extensions,
            layout.plan_tools,
            layout.content_store,
            layout.run_workspaces,
            layout.managed_workspaces,
            layout.executor_control,
            layout.local_launches,
            layout.local_launch_bindings,
            layout.local_launch_access,
            layout.diagnostics_log.parent,
        ):
            healthy_directory = root / "healthy" / target.name
            healthy_directory.mkdir(mode=0o700)
            target.mkdir(mode=0o700)
            subprocess.run(["mount", "--bind", str(healthy_directory), str(target)], check=True)
            bindings.append(target)
            assert target.stat().st_dev == root.stat().st_dev
        try:
            selected_database = LocalDatabase(database_path, journal_mode=journal_mode)
        except RuntimeError as error:
            assert journal_mode == "WAL"
            (root / "unavailable.json").write_text(
                json.dumps({"journal_mode": journal_mode, "reason": str(error)})
            )
            return 77
        assert selected_database.journal_mode == journal_mode
        runtime = ApplicationRuntime.build(standard_local=True, startup_purpose="workflow")
        with LocalDatabase.observe_existing_for_inspection(database_path) as connection:
            assert (
                str(connection.execute("PRAGMA journal_mode").fetchone()[0]).upper() == journal_mode
            )
            assert connection.execute("PRAGMA synchronous").fetchone()[0] == 2
        repository = root / "repositories/target"
        runtime.locations.add(repository.parent)
        plan = root / "workflow/plan.yml"
        runtime.plans.lock(plan)
        plan_target = runtime.plans.prepare_execution_target(plan, repository=repository)
        healthy = runtime.runs.start_prepared_plan(
            plan_target,
            repository_ids={repository_id(repository)},
            fleet_run_id="sqlite-pressure-control",
        )
        healthy_detail = runtime.runs.wait_for_terminal(healthy.fleet_run_id, timeout=60)
        healthy_step = healthy_detail.game_tape.repositories[0].steps[0]
        assert healthy_detail.game_tape.status == "PASSED"
        artifact = healthy_step.artifacts[0]
        assert isinstance(artifact, PresentPlanArtifact) and artifact.retained_object is not None
        historical_run = healthy_detail.run.model_dump(mode="json")
        historical_revision = healthy_detail.game_tape.journal_revision
        assert historical_revision is not None
        historical_tape = healthy_detail.game_tape.model_dump(
            mode="json", exclude={"journal_revision"}
        )
        assert (root / "applications-started").read_text() == "started\n"
        (root / "fault-enabled").touch()
        ready_fd = os.open(root / "ready", os.O_RDONLY | os.O_NONBLOCK)
        started = runtime.runs.start_prepared_plan(
            plan_target,
            repository_ids={repository_id(repository)},
            fleet_run_id="sqlite-pressure-fault",
        )
        run_id = started.fleet_run_id
        deadline = monotonic() + 45
        payload = b""
        while not payload:
            assert monotonic() < deadline, (
                "native application did not reach the SQLite pressure barrier"
            )
            if select.select([ready_fd], [], [], 0.05)[0]:
                payload = os.read(ready_fd, 8192)
        native = json.loads(payload)
        observer = LocalDatabase(database_path, read_only=True)
        lease = ExecutionCoordinator(observer).active_lease()
        assert lease is not None and lease.fleet_run_id == run_id and lease.custody is not None
        assert lease.custody.phase == "WORKER_OWNED"
        worker_pid = lease.custody.owner_pid
        assert SystemLeaseEnvironment().process_identity(worker_pid) == lease.custody.owner_identity
        before = runtime.runs.events(run_id)
        ancestors = [native["pid"]]
        ancestor = native["pid"]
        for _ in range(8):
            status = Path(f"/proc/{ancestor}/status").read_text()
            ancestor = int(
                next(line.split()[1] for line in status.splitlines() if line.startswith("PPid:"))
            )
            if ancestor == worker_pid:
                break
            ancestors.append(ancestor)
        else:
            raise AssertionError("held application has no bounded claimed-worker ancestry")
        app_events = [
            event
            for event in before.events
            if type(event) is ProcessStartedEvent
            and event.pid in ancestors
            and event.step_id == healthy_step.step_id
        ]
        assert len(app_events) == 1
        physical_id = app_events[0].physical_execution_id
        command_pid = app_events[0].pid
        command_birth = observe_process_birth(command_pid)
        assert command_birth is not None
        assert app_events[0].process_identity == (
            f"{command_birth.boot_id}:{command_birth.start_ticks}"
        )
        for pid in {worker_pid, native["pid"], command_pid}:
            birth = observe_process_birth(pid)
            assert birth is not None
            descriptors.append(os.pidfd_open(pid))
            assert observe_process_birth(pid) == birth
        checkpoint = None
        if journal_mode == "WAL":
            with closing(
                sqlite3.connect(database_path, isolation_level=None, timeout=5)
            ) as connection:
                checkpoint = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            assert checkpoint == (0, 0, 0), "native WAL checkpoint could not fully settle"
            assert database_path.with_name(database_path.name + "-wal").stat().st_size == 0
        thread_ids = sorted(int(path.name) for path in Path(f"/proc/{worker_pid}/task").iterdir())
        tracer = subprocess.Popen(
            [
                "strace",
                "-qq",
                "-s",
                "0",
                "-yy",
                "-e",
                "trace=write,pwrite64,writev,fsync,fdatasync,ftruncate",
                "-e",
                "status=failed",
                "-o",
                str(root / "kernel.trace"),
                *(item for tid in thread_ids for item in ("-p", str(tid))),
            ],
            stdout=subprocess.DEVNULL,
            stderr=trace_errors,
        )
        deadline = monotonic() + 5
        while True:
            attached = [
                int(
                    next(
                        line.split()[1]
                        for line in Path(f"/proc/{tid}/status").read_text().splitlines()
                        if line.startswith("TracerPid:")
                    )
                )
                for tid in thread_ids
            ]
            if all(pid == tracer.pid for pid in attached):
                break
            assert tracer.poll() is None and monotonic() < deadline
            select.select([], [], [], 0.01)
        filler_bytes = _fill_mount(mount)
        release = os.open(root / "hold", os.O_WRONLY | os.O_NONBLOCK)
        try:
            os.write(release, b"x")
        finally:
            os.close(release)
        for descriptor in descriptors:
            assert select.select([descriptor], [], [], 30)[0], (
                "original native process survived database pressure"
            )
        wait_observation: dict[str, Any]
        try:
            unexpected = runtime.runs.wait_for_terminal(run_id, timeout=5)
        except (StateConflict, sqlite3.DatabaseError) as error:
            wait_observation = {
                "class": type(error).__name__,
                "code": getattr(error, "sqlite_errorcode", None),
                "message": str(error),
            }
        else:
            raise AssertionError(
                f"Unwritable SQLite acquired a terminal result: {unexpected.run.status}"
            )
        assert tracer.wait(timeout=5) == 0
        trace = (root / "kernel.trace").read_bytes()
        assert len(trace) <= 128 * 1024
        failures = [
            line
            for line in trace.decode().splitlines()
            if "ENOSPC" in line and str(database_path) in line and "owned-filler" not in line
        ]
        assert failures, "no claimed-worker SQLite write returned native ENOSPC"
        # Space restoration is the only repair. Native SQLite, the original
        # Journal and existing scoped recovery own all resulting decisions.
        (mount / "owned-filler").unlink()
        with LocalDatabase.observe_existing_for_inspection(database_path) as connection:
            integrity = [tuple(row) for row in connection.execute("PRAGMA integrity_check")]
        (root / "integrity.json").write_text(json.dumps(integrity))
        assert integrity == [("ok",)], integrity
        interrupted = runtime.runs.get(run_id)
        assert not interrupted.run.status.terminal and interrupted.operations == ()
        assert interrupted.run.progress.completed < interrupted.run.progress.total
        runtime.recovery.recover_worker_exit(run_id, quiescence_timeout_seconds=5)
        recovered = runtime.runs.get(run_id)
        assert isinstance(recovered.run, FailedFleetRun)
        assert recovered.run.failure.code == "REPOSITORY_EFFECT_OUTCOME_UNKNOWN"
        assert recovered.operations == ()
        events = runtime.runs.events(run_id)
        assert sum(event.event_type.value == "RUN_STARTED" for event in events.events) == 1
        assert sum(event.event_type.value == "RUN_FAILED" for event in events.events) == 1
        app_final = [
            event
            for event in events.events
            if getattr(event, "physical_execution_id", None) == physical_id
        ]
        assert any(event.event_type.value == "PROCESS_STATE_UNKNOWN" for event in app_final)
        assert any(event.event_type.value == "OUTPUT_QUARANTINED" for event in app_final)
        assert not any(
            event.event_type.value in {"PROCESS_EXITED", "OUTPUT_FINALIZED"} for event in app_final
        )
        assert (
            ExecutionCoordinator(LocalDatabase(database_path, read_only=True)).active_lease()
            is None
        )
        starts = (root / "applications-started").read_text()
        assert starts == "started\nstarted\n"
        assert (root / "applications-finished").read_text() == "finished\nfinished\n"
        recorded_run, recorded_tape = (
            recovered.run.model_dump(mode="json"),
            recovered.game_tape.model_dump(mode="json"),
        )
        runtime.shutdown(wait_for_runs=True)
        runtime = ApplicationRuntime.build(standard_local=True, startup_purpose="workflow")
        reopened = runtime.runs.get(run_id)
        assert reopened.run.model_dump(mode="json") == recorded_run
        assert reopened.game_tape.model_dump(mode="json") == recorded_tape
        retained_control = runtime.runs.get(healthy.fleet_run_id)
        assert retained_control.run.model_dump(mode="json") == historical_run
        current_revision = retained_control.game_tape.journal_revision
        assert current_revision is not None and current_revision > historical_revision
        retained_tape = retained_control.game_tape.model_dump(
            mode="json", exclude={"journal_revision"}
        )
        if retained_tape != historical_tape:
            (root / "healthy-history-mismatch.json").write_text(
                json.dumps({"before": historical_tape, "after": retained_tape})
            )
        assert retained_tape == historical_tape
        assert _cli(root, "runs", "show", run_id)["outcome"] == "FAILED"
        _cli(root, "runs", "tape", run_id)
        exported = root / "healthy-export.bin"
        _cli(
            root,
            "runs",
            "export-artifact",
            healthy.fleet_run_id,
            "--step",
            healthy_step.step_id,
            "--path",
            "model.bin",
            "--output",
            str(exported),
        )
        assert sha256_file_with_size(exported) == (artifact.sha256, artifact.byte_count)
        assert (root / "applications-started").read_text() == starts
        assert not list(layout.run_workspaces.iterdir())
        report = {
            "journal_mode": journal_mode,
            "mount_bytes": 16 * MIB,
            "checkpoint": checkpoint,
            "worker_pid": worker_pid,
            "worker_identity": lease.custody.owner_identity,
            "application_physical_id": physical_id,
            "filler_bytes": filler_bytes,
            "failed_sqlite_syscalls": [line.replace(str(root), "<fixture>") for line in failures],
            "wait_observation": wait_observation,
            "unwritable_run_was_not_terminal": True,
            "recovered_status": recovered.run.status.value,
            "recovered_failure": recovered.run.failure.model_dump(mode="json"),
            "healthy_artifact_sha256": artifact.sha256,
            "healthy_history_readcut_before": historical_revision,
            "healthy_history_readcut_after": current_revision,
            "fresh_readback": True,
            "no_replay": True,
        }
        (root / "result.json").write_text(json.dumps(report, sort_keys=True))
        return 0
    finally:
        (mount / "owned-filler").unlink(missing_ok=True)
        for descriptor in descriptors:
            try:
                if not select.select([descriptor], [], [], 0)[0]:
                    signal.pidfd_send_signal(descriptor, signal.SIGKILL)
                    assert select.select([descriptor], [], [], 5)[0]
            finally:
                os.close(descriptor)
        if runtime is not None:
            runtime.shutdown(wait_for_runs=True)
        if ready_fd is not None:
            os.close(ready_fd)
        if tracer is not None and tracer.poll() is None:
            tracer.terminate()
            tracer.wait(timeout=5)
        trace_errors.close()
        for target in reversed(bindings):
            subprocess.run(["umount", str(target)], check=True)
        subprocess.run(["umount", str(mount)], check=True)


@pytest.mark.parametrize("journal_mode", ("DELETE", "WAL"))
def test_claimed_run_sqlite_enospc_retains_unknown_result_then_recovers(
    tmp_path: Path, journal_mode: str
) -> None:
    namespace = [
        "unshare",
        "--user",
        "--map-root-user",
        "--mount",
        "--propagation",
        "private",
        "--pid",
        "--fork",
        "--mount-proc",
        "--kill-child=KILL",
    ]
    probe = subprocess.run([*namespace, "true"], capture_output=True, timeout=10)
    if probe.returncode:
        pytest.skip(
            "native private user/mount/PID namespace unavailable: " + probe.stderr.decode()[:512]
        )
    root = tmp_path / "sqlite-pressure"
    root.mkdir(mode=0o700)
    _package(root)
    environment = _environment(root, journal_mode)
    with (
        (root / "native.stdout").open("w+b") as output,
        (root / "native.stderr").open("w+b") as errors,
    ):
        process = subprocess.Popen(
            [
                *namespace,
                sys.executable,
                "-I",
                str(Path(__file__).resolve()),
                str(root),
                journal_mode,
            ],
            env=environment,
            stdout=output,
            stderr=errors,
        )
        try:
            process.wait(timeout=150)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
        output.seek(0)
        errors.seek(0)
        body, diagnostic = output.read(256 * 1024 + 1), errors.read(128 * 1024 + 1)
    assert len(body) <= 256 * 1024 and len(diagnostic) <= 128 * 1024
    if process.returncode == 77 and (root / "unavailable.json").exists():
        pytest.skip(json.loads((root / "unavailable.json").read_text())["reason"])
    assert process.returncode == 0, (body[-8192:], diagnostic[-8192:])
    result = json.loads((root / "result.json").read_text())
    print("RUN_SQLITE_REAL_ENOSPC=" + json.dumps(result, sort_keys=True), flush=True)


if __name__ == "__main__":
    raise SystemExit(_witness(Path(sys.argv[1]), sys.argv[2]))
