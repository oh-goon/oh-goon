"""Real retained-artifact ENOSPC through the original claimed Run worker.

Only an eight-MiB tmpfs in a disposable user/mount/PID namespace is filled.
The database, Journal, native application and execution scratch stay on healthy
storage. Failed native syscalls prove the fault site; their trace is not Run
result authority and never captures application bytes.
"""

from __future__ import annotations

import errno
import json
import os
import re
import select
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from time import monotonic
from typing import Any

import pytest
import yaml

from oh_goon.application import ApplicationRuntime
from oh_goon.core.file_digest import sha256_file_with_size
from oh_goon.core.models.fleet_plan_evidence import PresentPlanArtifact
from oh_goon.core.models.fleet_plan_step_primitives import PLAN_CONTRACT_RETURN_CODE
from oh_goon.core.path_layouts import ApplicationStatePathLayout
from oh_goon.core.process_birth import observe_process_birth
from oh_goon.core.product_identity import repository_id
from oh_goon.integrations.fleet_content_store import FleetContentStore
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
        reason="real storage faults require native Linux namespaces, mount, strace and uv",
    ),
]

_PRODUCER = """import json
import os
from pathlib import Path
import sys

def main():
    size, barrier, ready, hold = sys.argv[1:]
    if barrier == "yes":
        with Path(ready).open("w") as stream:
            stream.write(json.dumps({"pid": os.getpid(), "cwd": str(Path.cwd())}))
        with Path(hold).open("rb") as stream:
            stream.read(1)
    with Path("model.bin").open("wb") as stream:
        for _ in range(int(size)):
            stream.write(b"model output\\n".ljust(1024, b"x"))
    print("native-output-complete", flush=True)
    return 0
"""


def _package(root: Path) -> None:
    from tests.factories.python_goon_projects import write_isolated_build_project

    repository = root / "repositories/target"
    repository.mkdir(parents=True)
    (repository / "subject.txt").write_text("storage fault subject\n")
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    package = root / "workflow/goons/general/isolated-build"
    write_isolated_build_project(package)
    (package / "isolated_app.py").write_text(_PRODUCER)
    for name in ("ready", "hold"):
        os.mkfifo(root / name)
    (package / "goon.yml").write_text(
        yaml.safe_dump(
            {
                "runtime": "python",
                "uv": {"lock_mode": "frozen"},
                "inputs": {
                    "size": {"type": "integer", "default": 1},
                    "barrier": {"type": "string", "default": "no"},
                },
                "steps": [
                    {
                        "id": "produce",
                        "run": "isolated-build ${{ inputs.size }} ${{ inputs.barrier }} "
                        + " ".join(shlex.quote(str(root / name)) for name in ("ready", "hold")),
                        "artifacts": [{"path": "model.bin", "retention": "content_store"}],
                    }
                ],
            },
            sort_keys=False,
        )
    )
    (root / "workflow/plan.yml").write_text(
        "goons:\n- import: general/isolated-build\n  from: goons\n"
    )


def _environment(root: Path) -> dict[str, str]:
    environment = dict(os.environ)
    environment.pop("OH_GOON_EXECUTORS_FILE", None)
    environment.update(
        OH_GOON_DB=str(root / "data/oh-goon/runtime.sqlite"),
        OH_GOON_LEAGUE_FILE=str(root / "config/league.yml"),
        XDG_CONFIG_HOME=str(root / "config"),
        XDG_DATA_HOME=str(root / "data"),
        XDG_STATE_HOME=str(root / "state"),
        XDG_CACHE_HOME=str(root / "cache"),
        UV_CACHE_DIR=str(root / "uv-cache"),
        UV_PYTHON=sys.executable,
        UV_PYTHON_DOWNLOADS="never",
    )
    for name in ("config", "data", "state", "cache", "uv-cache"):
        (root / name).mkdir(parents=True, mode=0o700)
    return environment


def _cli(root: Path, *arguments: str) -> dict[str, Any]:
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        completed = subprocess.run(
            [str(Path(sys.executable).with_name("oh-goon")), *arguments, "--json"],
            cwd=root,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=errors,
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


def _fill_mount(root: Path) -> int:
    descriptor = os.open(root / "owned-filler", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    written = 0
    try:
        while written <= 9 * MIB:
            try:
                written += os.write(descriptor, b"x" * 65536)
            except OSError as error:
                assert error.errno == errno.ENOSPC
                return written
        raise AssertionError("dedicated mount did not return native ENOSPC")
    finally:
        os.close(descriptor)


def _witness(root: Path, boundary: str) -> None:
    layout = ApplicationStatePathLayout.from_database(Path(os.environ["OH_GOON_DB"]))
    # Staging and its atomic object destination must stay on the same native
    # filesystem. The reference record has its own same-directory publication.
    mount = layout.content_store if boundary == "staging" else layout.content_store / "references"
    mount.mkdir(parents=True, mode=0o700)
    subprocess.run(
        [
            "mount",
            "-t",
            "tmpfs",
            "-o",
            "size=8m,nr_inodes=512,mode=0700,nodev,nosuid,noexec",
            "tmpfs",
            str(mount),
        ],
        check=True,
    )
    assert mount.resolve() == mount and not mount.is_symlink() and not list(mount.iterdir())
    matches = []
    for line in Path("/proc/self/mountinfo").read_text().splitlines():
        left, _, right = line.partition(" - ")
        fields = left.split()
        observed_path = re.sub(r"\\([0-7]{3})", lambda value: chr(int(value[1], 8)), fields[4])
        if observed_path == str(mount):
            matches.append((fields[5].split(","), right.split()[0]))
    assert len(matches) == 1 and matches[0][1] == "tmpfs"
    assert {"nodev", "nosuid", "noexec"}.issubset(matches[0][0])
    descriptor = os.open(mount, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        metadata, capacity = os.fstat(descriptor), os.fstatvfs(descriptor)
        assert metadata.st_uid == os.getuid() and stat.S_IMODE(metadata.st_mode) == 0o700
        assert metadata.st_dev != layout.root.stat().st_dev
        assert capacity.f_blocks * capacity.f_frsize == 8 * MIB
        assert capacity.f_bavail * capacity.f_frsize == 8 * MIB
        assert capacity.f_files <= 512
    finally:
        os.close(descriptor)
    facts = {"filesystem": "tmpfs", "total_bytes": 8 * MIB, "max_inodes": capacity.f_files}
    repository = root / "repositories/target"
    owner = repository_id(repository)
    plan = root / "workflow/plan.yml"
    runtime = ApplicationRuntime.build(standard_local=True)
    running: str | None = None
    filler_bytes: int | None = None
    ready_fd: int | None = None
    pidfds: list[int] = []
    tracer: subprocess.Popen[bytes] | None = None
    trace_errors = (root / "trace.stderr").open("wb")
    try:
        runtime.locations.add(repository.parent)
        runtime.plans.lock(plan)
        target = runtime.plans.prepare_execution_target(plan, repository=repository)
        control = runtime.runs.start_prepared_plan(
            target, repository_ids={owner}, fleet_run_id=f"storage-{boundary}-control"
        )
        healthy = runtime.runs.wait_for_terminal(control.fleet_run_id, timeout=60)
        healthy_step = healthy.game_tape.repositories[0].steps[0]
        assert healthy.game_tape.status == "PASSED", healthy.model_dump(mode="json")
        artifact = healthy_step.artifacts[0]
        assert isinstance(artifact, PresentPlanArtifact) and artifact.retained_object is not None
        assert healthy_step.settlement.observed_returncode == 0
        store = FleetContentStore(layout.content_store)
        protected = store.get(artifact.retained_object.reference_id, owner_repository_id=owner)
        assert sha256_file_with_size(protected.path) == (artifact.sha256, artifact.byte_count)
        inputs: dict[str, Any] = {
            "size": 16 * 1024 if boundary == "staging" else 2,
            "barrier": "yes",
        }
        runtime.plans.lock(plan, inputs=inputs)
        fault_target = runtime.plans.prepare_execution_target(
            plan, repository=repository, inputs=inputs
        )
        ready_fd = os.open(root / "ready", os.O_RDONLY | os.O_NONBLOCK)
        started = runtime.runs.start_prepared_plan(
            fault_target, repository_ids={owner}, fleet_run_id=f"storage-{boundary}-fault"
        )
        running = started.fleet_run_id
        deadline = monotonic() + 45
        payload = b""
        while not payload:
            assert monotonic() < deadline, "native application did not reach its barrier"
            if select.select([ready_fd], [], [], 0.05)[0]:
                payload = os.read(ready_fd, 8192)
        native = json.loads(payload)
        assert Path(native["cwd"]).is_dir()
        # This is the existing canonical worker claim, not a test-owned executor.
        database = LocalDatabase(Path(os.environ["OH_GOON_DB"]), read_only=True)
        lease = ExecutionCoordinator(database).active_lease()
        assert lease is not None and lease.fleet_run_id == running and lease.custody is not None
        assert lease.custody.phase == "WORKER_OWNED"
        assert lease.custody.owner_pid != os.getpid()
        birth = observe_process_birth(lease.custody.owner_pid)
        assert birth is not None
        assert (
            SystemLeaseEnvironment().process_identity(lease.custody.owner_pid)
            == lease.custody.owner_identity
        )
        pidfds.append(os.pidfd_open(lease.custody.owner_pid))
        assert observe_process_birth(lease.custody.owner_pid) == birth
        assert (
            SystemLeaseEnvironment().process_identity(lease.custody.owner_pid)
            == lease.custody.owner_identity
        )
        pidfds.append(os.pidfd_open(native["pid"]))
        before = runtime.runs.events(running)
        assert any(event.event_type.value == "PROCESS_STARTING" for event in before.events)
        reference_files = {
            path.relative_to(layout.content_store / "references")
            for path in (layout.content_store / "references").rglob("*")
            if path.is_file()
        }
        # Observe the already-launched worker only. Following future children
        # would perturb the original native preexec/startup custody handshake.
        thread_ids = sorted(
            int(path.name) for path in Path(f"/proc/{lease.custody.owner_pid}/task").iterdir()
        )
        tracer = subprocess.Popen(
            [
                "strace",
                "-qq",
                "-s",
                "0",
                "-yy",
                "-e",
                "trace=write,pwrite64,writev,fsync,fdatasync",
                "-e",
                "status=failed",
                "-o",
                str(root / "kernel.trace"),
                *(argument for tid in thread_ids for argument in ("-p", str(tid))),
            ],
            stdout=subprocess.DEVNULL,
            stderr=trace_errors,
        )
        deadline = monotonic() + 5
        while True:
            statuses = [
                Path(f"/proc/{lease.custody.owner_pid}/task/{tid}/status").read_text()
                for tid in thread_ids
            ]
            tracer_pids = [
                int(
                    next(
                        line.split()[1]
                        for line in status.splitlines()
                        if line.startswith("TracerPid:")
                    )
                )
                for status in statuses
            ]
            if all(pid == tracer.pid for pid in tracer_pids):
                break
            assert tracer.poll() is None and monotonic() < deadline, (
                "native worker trace did not attach"
            )
            select.select([], [], [], 0.01)
        if boundary == "references":
            filler_bytes = _fill_mount(mount)
        with (root / "hold").open("wb") as channel:
            channel.write(b"x")
        failed = runtime.runs.wait_for_terminal(running, timeout=60)
        failed_step = failed.game_tape.repositories[0].steps[0]
        assert failed.run.status.value == "COMPLETED"
        assert failed.game_tape.status == "ATTENTION_REQUIRED"
        assert failed_step.settlement.outcome == "FAILED"
        assert failed_step.exit_code == PLAN_CONTRACT_RETURN_CODE
        assert failed_step.settlement.observed_returncode == 0
        assert failed_step.settlement.cleanup == "VERIFIED"
        assert len(failed_step.attempts) == 1 and failed_step.artifacts == ()
        assert failed_step.attempts[0].returncode == 0
        assert not layout.run_workspaces.exists() or not list(layout.run_workspaces.iterdir())
        assert not list((layout.content_store / "staging").iterdir())
        assert not Path(native["cwd"]).exists()
        for descriptor in pidfds:
            assert select.select([descriptor], [], [], 5)[0], "original process did not retire"
        assert tracer.wait(timeout=5) == 0
        (mount / "owned-filler").unlink(missing_ok=True)
        assert {
            path.relative_to(layout.content_store / "references")
            for path in (layout.content_store / "references").rglob("*")
            if path.is_file()
        } == reference_files
        recorded = failed.run.model_dump(mode="json")
        tape = failed.game_tape.model_dump(mode="json")
        runtime.shutdown(wait_for_runs=True)
        runtime = ApplicationRuntime.build(standard_local=True)
        reopened = runtime.runs.get(running)
        assert reopened.run.model_dump(mode="json") == recorded
        assert reopened.game_tape.model_dump(mode="json") == tape
        assert _cli(root, "runs", "show", running)["outcome"] == "ATTENTION_REQUIRED"
        public_tape = _cli(root, "runs", "tape", running)
        assert public_tape["repositories"][0]["steps"][0]["settlement"]["outcome"] == "FAILED"
        assert (
            _cli(root, "runs", "outputs", running)["repositories"][0]["steps"][0]["artifacts"] == []
        )
        exported = root / "control-export.bin"
        _cli(
            root,
            "runs",
            "export-artifact",
            control.fleet_run_id,
            "--step",
            healthy_step.step_id,
            "--path",
            "model.bin",
            "--output",
            str(exported),
        )
        assert sha256_file_with_size(exported) == (artifact.sha256, artifact.byte_count)
        final_events = runtime.runs.events(running)
        assert final_events.run.fleet_run_id == running and not final_events.has_more
        assert sum(event.event_type.value == "RUN_COMPLETED" for event in final_events.events) == 1
        assert healthy_step.project_source is not None
        assert failed_step.project_source == healthy_step.project_source
        report = {
            "boundary": boundary,
            "mount": facts,
            "healthy_run": control.fleet_run_id,
            "failed_run": running,
            "native_returncode": failed_step.settlement.observed_returncode,
            "step_outcome": failed_step.settlement.outcome,
            "step_exit_code": failed_step.exit_code,
            "run_status": recorded["status"],
            "cleanup": failed_step.settlement.cleanup,
            "filler_bytes": filler_bytes,
            "previous_artifact_sha256": artifact.sha256,
            "project_source_sha256": healthy_step.project_source.content_sha256,
            "native_lock_sha256": sha256_file_with_size(
                plan.parent / "goons/general/isolated-build/uv.lock"
            )[0],
            "fresh_runtime_and_cli_readback": True,
        }
        (root / "result.json").write_text(json.dumps(report, sort_keys=True))
    finally:
        try:
            if running is not None and not runtime.runs.get(running).run.status.terminal:
                runtime.runs.cancel(running)
            runtime.shutdown(wait_for_runs=True)
        finally:
            if ready_fd is not None:
                os.close(ready_fd)
            for descriptor in pidfds:
                os.close(descriptor)
            if tracer is not None and tracer.poll() is None:
                tracer.terminate()
                tracer.wait(timeout=5)
            trace_errors.close()
            (mount / "owned-filler").unlink(missing_ok=True)
            subprocess.run(["umount", str(mount)], check=True)


@pytest.mark.parametrize("boundary", ("staging", "references"))
def test_claimed_run_reports_real_retention_enospc(tmp_path: Path, boundary: str) -> None:
    probe = subprocess.run(
        [
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
            "true",
        ],
        capture_output=True,
        timeout=10,
    )
    if probe.returncode:
        pytest.skip(
            "native private user/mount/PID namespace unavailable: " + probe.stderr.decode()[:512]
        )
    root = tmp_path / "storage-fault"
    root.mkdir(mode=0o700)
    _package(root)
    environment = _environment(root)
    trace = root / "kernel.trace"
    command = [
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
        sys.executable,
        "-I",
        str(Path(__file__).resolve()),
        str(root),
        boundary,
    ]
    with (
        (root / "native.stdout").open("w+b") as output,
        (root / "native.stderr").open("w+b") as errors,
    ):
        process = subprocess.Popen(command, env=environment, stdout=output, stderr=errors)
        try:
            process.wait(timeout=180)
        finally:
            if process.poll() is None:
                process.send_signal(signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        output.seek(0)
        errors.seek(0)
        body, diagnostic = output.read(128 * 1024 + 1), errors.read(128 * 1024 + 1)
    assert len(body) <= 128 * 1024 and len(diagnostic) <= 128 * 1024
    assert process.returncode == 0, (body[-8192:], diagnostic[-8192:])
    with trace.open("rb") as stream:
        native_trace = stream.read(128 * 1024 + 1)
    assert len(native_trace) <= 128 * 1024
    mount_path = str(root / "data/oh-goon/content-store" / boundary)
    failures = [
        line
        for line in native_trace.decode().splitlines()
        if "ENOSPC" in line and mount_path in line and "owned-filler" not in line
    ]
    assert failures, "no product retention write returned native ENOSPC on the selected mount"
    result = json.loads((root / "result.json").read_text())
    result.update(
        kernel_errno=errno.ENOSPC,
        failed_product_syscalls=[line.replace(str(root), "<fixture>") for line in failures],
    )
    print("RUN_STORAGE_REAL_ENOSPC=" + json.dumps(result, sort_keys=True), flush=True)


if __name__ == "__main__":
    _witness(Path(sys.argv[1]), sys.argv[2])
