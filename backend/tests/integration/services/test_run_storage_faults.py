"""Real retained-artifact and native uv-build ENOSPC in the claimed Run worker.

Only an eight-MiB tmpfs in a disposable user/mount/PID namespace is filled.
The database, Journal, native application and execution scratch stay on healthy
storage for artifact faults. The uv case bounds owned Run scratch instead, while
its database, Journal and retained content stay healthy. Failed native syscalls
prove the fault site; their trace is not Run result authority or application bytes.
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
from oh_goon.core.models.fleet_plan_evidence import MissingPlanArtifact, PresentPlanArtifact
from oh_goon.core.models.fleet_plan_step_primitives import PLAN_CONTRACT_RETURN_CODE
from oh_goon.core.models.run_events import (
    DescendantsQuiescentEvent,
    ProcessExitedEvent,
    ProcessStartedEvent,
)
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


_UV_BACKEND = """import json
import os
from pathlib import Path
import zipfile

def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    project = Path(__file__).parent
    if Path(FLAG).exists():
        with Path(READY).open('w') as stream:
            stream.write(json.dumps({'pid': os.getpid(), 'build_parent_pid': os.getppid(),
                'cwd': str(project.resolve()),
                'wheel_directory': str(Path(wheel_directory).resolve())}))
        with Path(HOLD).open('rb') as stream:
            stream.read(1)
    filename = 'isolated_build-1.0.0-py3-none-any.whl'
    with zipfile.ZipFile(Path(wheel_directory) / filename, 'w') as wheel:
        wheel.writestr('isolated_app.py', (project / 'isolated_app.py').read_text())
        wheel.writestr('isolated_build-1.0.0.dist-info/METADATA',
            'Metadata-Version: 2.1\\nName: isolated-build\\nVersion: 1.0.0\\n')
        wheel.writestr('isolated_build-1.0.0.dist-info/WHEEL',
            'Wheel-Version: 1.0\\nRoot-Is-Purelib: true\\nTag: py3-none-any\\n')
        wheel.writestr('isolated_build-1.0.0.dist-info/entry_points.txt',
            '[console_scripts]\\nisolated-build = isolated_app:main\\n')
        wheel.writestr('isolated_build-1.0.0.dist-info/RECORD', '')
    return filename
"""


def _package(root: Path, *, uv_preparation: bool = False) -> None:
    from tests.factories.python_goon_projects import write_isolated_build_project

    repository = root / "repositories/target"
    repository.mkdir(parents=True)
    (repository / "subject.txt").write_text("storage fault subject\n")
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    package = root / "workflow/goons/general/isolated-build"
    write_isolated_build_project(package)
    producer = _PRODUCER
    if uv_preparation:
        producer = producer.replace(
            "def main():\n",
            f"def main():\n    Path({str(root / 'application-ran')!r}).write_text('ran')\n",
        )
        (package / "backend.py").write_text(
            _UV_BACKEND.replace("FLAG", repr(str(root / "uv-fault-enabled")))
            .replace("READY", repr(str(root / "build-ready")))
            .replace("HOLD", repr(str(root / "build-hold")))
        )
        os.mkfifo(root / "build-ready")
        os.mkfifo(root / "build-hold")
    (package / "isolated_app.py").write_text(producer)
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
    uv_preparation = boundary == "uv-preparation"
    mount = (
        layout.run_workspaces
        if uv_preparation
        else layout.content_store
        if boundary == "staging"
        else layout.content_store / "references"
    )
    mount.mkdir(parents=True, mode=0o700)
    subprocess.run(
        [
            "mount",
            "-t",
            "tmpfs",
            "-o",
            "size=8m,nr_inodes=512,mode=0700,nodev,nosuid" + ("" if uv_preparation else ",noexec"),
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
    assert {"nodev", "nosuid"}.issubset(matches[0][0])
    assert ("noexec" in matches[0][0]) is not uv_preparation
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
    uv_boundary: dict[str, Any] | None = None
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
        if uv_preparation:
            assert (root / "application-ran").read_text() == "ran"
            (root / "application-ran").unlink()
            (root / "uv-fault-enabled").touch()
        inputs: dict[str, Any] = {
            "size": 16 * 1024 if boundary == "staging" else 2,
            "barrier": "no" if uv_preparation else "yes",
        }
        runtime.plans.lock(plan, inputs=inputs)
        fault_target = runtime.plans.prepare_execution_target(
            plan, repository=repository, inputs=inputs
        )
        ready_fd = os.open(
            root / ("build-ready" if uv_preparation else "ready"), os.O_RDONLY | os.O_NONBLOCK
        )
        started = runtime.runs.start_prepared_plan(
            fault_target, repository_ids={owner}, fleet_run_id=f"storage-{boundary}-fault"
        )
        running = started.fleet_run_id
        deadline = monotonic() + 45
        payload = b""
        while not payload:
            assert monotonic() < deadline, "native workload did not reach its selected barrier"
            if select.select([ready_fd], [], [], 0.05)[0]:
                payload = os.read(ready_fd, 8192)
        native = json.loads(payload)
        assert Path(native["cwd"]).is_dir()
        if uv_preparation:
            wheel_directory = Path(native["wheel_directory"])
            assert wheel_directory.is_relative_to(layout.run_workspaces)
            assert wheel_directory.stat().st_dev == mount.stat().st_dev
            assert not (root / "application-ran").exists()
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
        before = runtime.runs.events(running)
        assert any(event.event_type.value == "PROCESS_STARTING" for event in before.events)
        native_pids: tuple[int, ...] = (native["pid"],)
        if uv_preparation:
            # Bind the held PEP517 backend to an already recorded uv process;
            # its direct parent can be an intermediate native build helper.
            ancestors: list[int] = []
            ancestor = native["pid"]
            for _ in range(8):
                status = Path(f"/proc/{ancestor}/status").read_text()
                ancestor = int(
                    next(
                        line.split()[1] for line in status.splitlines() if line.startswith("PPid:")
                    )
                )
                if ancestor == lease.custody.owner_pid:
                    break
                ancestors.append(ancestor)
            else:
                raise AssertionError("held native backend has no bounded claimed-worker ancestry")
            uv_events = [
                event
                for event in before.events
                if type(event) is ProcessStartedEvent and event.pid in ancestors
            ]
            assert len(uv_events) == 1
            uv_started = uv_events[0]
            assert uv_started.process_identity is not None
            uv_birth = observe_process_birth(uv_started.pid)
            assert uv_birth is not None
            # Native command events retain boot/start identity; the Run-worker
            # lease uses its separate provider-prefixed representation.
            assert uv_started.process_identity == f"{uv_birth.boot_id}:{uv_birth.start_ticks}"
            uv_executable = Path(f"/proc/{uv_started.pid}/exe").resolve(strict=True)
            uv_sha256, uv_bytes = sha256_file_with_size(uv_executable)
            with tempfile.TemporaryFile() as version_output:
                version = subprocess.run(
                    [str(uv_executable), "--version"],
                    stdout=version_output,
                    stderr=subprocess.STDOUT,
                    timeout=5,
                    env={"PATH": os.defpath, "HOME": str(root)},
                )
                version_output.seek(0)
                version_text = version_output.read(4097)
            assert version.returncode == 0 and len(version_text) <= 4096
            native_pids = (native["pid"], uv_started.pid)
            uv_boundary = {
                "backend_pid": native["pid"],
                "build_parent_pid": native["build_parent_pid"],
                "uv_pid": uv_started.pid,
                "uv_physical_execution_id": uv_started.physical_execution_id,
                "uv_journal_process_identity": uv_started.process_identity,
                "uv_birth": uv_birth.to_payload(),
                "uv_executable": str(uv_executable),
                "uv_executable_sha256": uv_sha256,
                "uv_executable_bytes": uv_bytes,
                "uv_version": version_text.decode("utf-8").strip(),
                "wheel_file": str(
                    (
                        Path(native["wheel_directory"]) / "isolated_build-1.0.0-py3-none-any.whl"
                    ).relative_to(root)
                ),
            }
            (root / "native-fault-boundary.json").write_text(
                json.dumps(uv_boundary, sort_keys=True)
            )
        for pid in native_pids:
            native_birth = observe_process_birth(pid)
            assert native_birth is not None
            pidfds.append(os.pidfd_open(pid))
            assert observe_process_birth(pid) == native_birth
        reference_files = {
            path.relative_to(layout.content_store / "references")
            for path in (layout.content_store / "references").rglob("*")
            if path.is_file()
        }
        # Observe the already-launched worker only. Following future children
        # would perturb the original native preexec/startup custody handshake.
        traced_pids = (
            (lease.custody.owner_pid, *native_pids)
            if uv_preparation
            else (lease.custody.owner_pid,)
        )
        thread_ids = sorted(
            {int(path.name) for pid in traced_pids for path in Path(f"/proc/{pid}/task").iterdir()}
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
            statuses = [Path(f"/proc/{tid}/status").read_text() for tid in thread_ids]
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
        if boundary == "references" or uv_preparation:
            filler_bytes = _fill_mount(mount)
        with (root / ("build-hold" if uv_preparation else "hold")).open("wb") as channel:
            channel.write(b"x")
        failed = runtime.runs.wait_for_terminal(running, timeout=60)
        failed_step = failed.game_tape.repositories[0].steps[0]
        assert failed.run.status.value == "COMPLETED"
        assert failed.game_tape.status == "ATTENTION_REQUIRED"
        assert failed_step.settlement.outcome == "FAILED"
        assert len(failed_step.attempts) == 1
        if uv_preparation:
            assert failed_step.exit_code not in (None, 0)
            assert failed_step.settlement.observed_returncode == failed_step.exit_code
            assert failed_step.attempts[0].returncode == failed_step.exit_code
            assert failed_step.attempts[0].tool is not None and uv_boundary is not None
            assert failed_step.attempts[0].tool.name == "uv"
            assert failed_step.attempts[0].tool.sha256 == uv_boundary["uv_executable_sha256"]
            preparation = failed_step.attempts[0].uv_preparation
            assert preparation is not None
            assert preparation.lock_mode == "frozen" and preparation.freshness == "not_checked"
            assert failed_step.artifacts == (MissingPlanArtifact(path="model.bin"),)
            assert not (root / "application-ran").exists()
        else:
            assert failed_step.exit_code == PLAN_CONTRACT_RETURN_CODE
            assert failed_step.settlement.observed_returncode == 0
            assert failed_step.attempts[0].returncode == 0
            assert failed_step.artifacts == ()
        assert failed_step.settlement.cleanup == "VERIFIED"
        (mount / "owned-filler").unlink(missing_ok=True)
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
        public_artifacts = _cli(root, "runs", "outputs", running)["repositories"][0]["steps"][0][
            "artifacts"
        ]
        assert public_artifacts == (
            [{"path": "model.bin", "status": "MISSING"}] if uv_preparation else []
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
        if uv_preparation:
            assert uv_boundary is not None
            preparation_events = [
                event
                for event in final_events.events
                if getattr(event, "physical_execution_id", None)
                == uv_boundary["uv_physical_execution_id"]
                and event.event_type.value
                in {
                    "PROCESS_STARTING",
                    "PROCESS_STARTED",
                    "PROCESS_EXITED",
                    "DESCENDANTS_QUIESCENT",
                }
            ]
            assert [event.event_type.value for event in preparation_events] == [
                "PROCESS_STARTING",
                "PROCESS_STARTED",
                "PROCESS_EXITED",
                "DESCENDANTS_QUIESCENT",
            ]
            exited = preparation_events[2]
            assert isinstance(exited, ProcessExitedEvent)
            assert exited.pid == uv_boundary["uv_pid"]
            assert exited.process_identity == uv_boundary["uv_journal_process_identity"]
            assert exited.returncode == failed_step.exit_code
            quiescent = preparation_events[-1]
            assert isinstance(quiescent, DescendantsQuiescentEvent)
            assert (
                quiescent.pid == exited.pid
                and quiescent.process_identity == exited.process_identity
            )
        assert healthy_step.project_source is not None
        assert failed_step.project_source == healthy_step.project_source
        report = {
            "boundary": boundary,
            "fault_scope": "native uv wheel build before application"
            if uv_preparation
            else "retained artifact publication",
            "mount": facts,
            "healthy_run": control.fleet_run_id,
            "failed_run": running,
            "native_returncode": failed_step.settlement.observed_returncode,
            "step_outcome": failed_step.settlement.outcome,
            "step_exit_code": failed_step.exit_code,
            "run_status": recorded["status"],
            "cleanup": failed_step.settlement.cleanup,
            "filler_bytes": filler_bytes,
            "uv_preparation": failed_step.attempts[0].uv_preparation.model_dump(mode="json")
            if uv_preparation and failed_step.attempts[0].uv_preparation is not None
            else None,
            "uv_boundary": uv_boundary,
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


def _run_fault_witness(tmp_path: Path, boundary: str) -> None:
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
    _package(root, uv_preparation=boundary == "uv-preparation")
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
    mount_path = str(
        root
        / "data/oh-goon"
        / ("run-workspaces" if boundary == "uv-preparation" else f"content-store/{boundary}")
    )
    failures = [
        line
        for line in native_trace.decode().splitlines()
        if "ENOSPC" in line and mount_path in line and "owned-filler" not in line
    ]
    if boundary == "uv-preparation":
        uv_boundary = json.loads((root / "native-fault-boundary.json").read_text())
        failures = [
            line
            for line in failures
            if re.match(rf"{uv_boundary['backend_pid']}\s+write\(", line)
            and str(root / uv_boundary["wheel_file"]) in line
        ]
    assert failures, "no product retention write returned native ENOSPC on the selected mount"
    result = json.loads((root / "result.json").read_text())
    result.update(
        kernel_errno=errno.ENOSPC,
        failed_product_syscalls=[line.replace(str(root), "<fixture>") for line in failures],
    )
    print("RUN_STORAGE_REAL_ENOSPC=" + json.dumps(result, sort_keys=True), flush=True)


@pytest.mark.parametrize("boundary", ("staging", "references"))
def test_claimed_run_reports_real_retention_enospc(tmp_path: Path, boundary: str) -> None:
    _run_fault_witness(tmp_path, boundary)


def test_claimed_run_reports_real_uv_preparation_enospc(tmp_path: Path) -> None:
    _run_fault_witness(tmp_path, "uv-preparation")


if __name__ == "__main__":
    _witness(Path(sys.argv[1]), sys.argv[2])
