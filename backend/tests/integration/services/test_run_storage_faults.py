"""Real artifact, native uv-build and passive log ENOSPC around claimed Runs.

Only an eight-MiB tmpfs in a disposable user/mount/PID namespace is filled.
The database, Journal, native application and execution scratch stay on healthy
storage for artifact faults. The uv case bounds owned Run scratch instead, while
its database, Journal and retained content stay healthy. Failed native syscalls
prove the fault site; their trace is not Run result authority or application bytes.
Log cases use a controlled diagnostic producer while a real Run is held active;
they preserve native success/failure without treating the log as result authority.
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
from oh_goon.core.models.diagnostic import MAX_DIAGNOSTIC_MESSAGE_CHARS, DiagnosticEvent
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


def _package(root: Path, *, uv_preparation: bool = False, diagnostic_exit: bool = False) -> None:
    from tests.factories.python_goon_projects import write_isolated_build_project

    repository = root / "repositories/target"
    repository.mkdir(parents=True)
    (repository / "subject.txt").write_text("storage fault subject\n")
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    package = root / "workflow/goons/general/isolated-build"
    write_isolated_build_project(package)
    producer = _PRODUCER
    inputs: dict[str, Any] = {
        "size": {"type": "integer", "default": 1},
        "barrier": {"type": "string", "default": "no"},
    }
    command = "isolated-build ${{ inputs.size }} ${{ inputs.barrier }} " + " ".join(
        shlex.quote(str(root / name)) for name in ("ready", "hold")
    )
    if diagnostic_exit:
        producer = producer.replace("sys.argv[1:]", "sys.argv[1:5]").replace(
            "return 0", "return int(sys.argv[5])"
        )
        inputs["native_exit"] = {"type": "integer", "default": 0}
        command += " ${{ inputs.native_exit }}"
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
                "inputs": inputs,
                "steps": [
                    {
                        "id": "produce",
                        "run": command,
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


def _private_mount(mount: Path, healthy_root: Path, *, executable: bool = False) -> dict[str, Any]:
    mount.mkdir(parents=True, mode=0o700)
    subprocess.run(
        [
            "mount",
            "-t",
            "tmpfs",
            "-o",
            "size=8m,nr_inodes=512,mode=0700,nodev,nosuid" + ("" if executable else ",noexec"),
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
    assert ("noexec" in matches[0][0]) is not executable
    descriptor = os.open(mount, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        metadata, capacity = os.fstat(descriptor), os.fstatvfs(descriptor)
        assert metadata.st_uid == os.getuid() and stat.S_IMODE(metadata.st_mode) == 0o700
        assert metadata.st_dev != healthy_root.stat().st_dev
        assert capacity.f_blocks * capacity.f_frsize == 8 * MIB
        assert capacity.f_bavail * capacity.f_frsize == 8 * MIB
        assert capacity.f_files <= 512
    finally:
        os.close(descriptor)
    return {"filesystem": "tmpfs", "total_bytes": 8 * MIB, "max_inodes": capacity.f_files}


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
    facts = _private_mount(mount, layout.root, executable=uv_preparation)
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


def _diagnostic_witness(root: Path, native_exit: int) -> None:
    """Fail a real sink append, without inventing a worker-authored diagnostic."""
    layout = ApplicationStatePathLayout.from_database(Path(os.environ["OH_GOON_DB"]))
    mount = layout.diagnostics_log.parent
    mount_facts = _private_mount(mount, layout.root)
    repository = root / "repositories/target"
    owner = repository_id(repository)
    plan = root / "workflow/plan.yml"
    runtime = ApplicationRuntime.build(standard_local=True)
    running: str | None = None
    ready_fd: int | None = None
    pidfds: list[int] = []
    tracer: subprocess.Popen[bytes] | None = None
    trace_errors = (root / "trace.stderr").open("wb")
    try:
        runtime.locations.add(repository.parent)
        expected_outcome = "SUCCESS" if native_exit == 0 else "ATTENTION_REQUIRED"
        expected_tape = "PASSED" if native_exit == 0 else "ATTENTION_REQUIRED"
        control_inputs: dict[str, Any] = {"size": 2, "barrier": "no", "native_exit": native_exit}
        runtime.plans.lock(plan, inputs=control_inputs)
        target = runtime.plans.prepare_execution_target(
            plan, repository=repository, inputs=control_inputs
        )
        control = runtime.runs.start_prepared_plan(
            target,
            repository_ids={owner},
            fleet_run_id=f"storage-diagnostics-{native_exit}-control",
        )
        healthy = runtime.runs.wait_for_terminal(control.fleet_run_id, timeout=60)
        healthy_step = healthy.game_tape.repositories[0].steps[0]
        assert healthy.game_tape.status == expected_tape, healthy.model_dump(mode="json")
        assert healthy.outcome.value == expected_outcome
        assert healthy_step.exit_code == healthy_step.settlement.observed_returncode == native_exit
        assert healthy_step.settlement.cleanup == "VERIFIED"
        artifact = healthy_step.artifacts[0]
        assert isinstance(artifact, PresentPlanArtifact) and artifact.retained_object is not None
        protected = FleetContentStore(layout.content_store).get(
            artifact.retained_object.reference_id, owner_repository_id=owner
        )
        assert protected.path.stat().st_dev != mount.stat().st_dev
        assert Path(os.environ["OH_GOON_DB"]).stat().st_dev != mount.stat().st_dev
        assert sha256_file_with_size(protected.path) == (artifact.sha256, artifact.byte_count)
        inputs: dict[str, Any] = {"size": 2, "barrier": "yes", "native_exit": native_exit}
        runtime.plans.lock(plan, inputs=inputs)
        fault_target = runtime.plans.prepare_execution_target(
            plan, repository=repository, inputs=inputs
        )
        ready_fd = os.open(root / "ready", os.O_RDONLY | os.O_NONBLOCK)
        started = runtime.runs.start_prepared_plan(
            fault_target,
            repository_ids={owner},
            fleet_run_id=f"storage-diagnostics-{native_exit}-fault",
        )
        running = started.fleet_run_id
        deadline = monotonic() + 45
        payload = b""
        while not payload:
            assert monotonic() < deadline, (
                "native application did not reach its log-pressure barrier"
            )
            if select.select([ready_fd], [], [], 0.05)[0]:
                payload = os.read(ready_fd, 8192)
        native = json.loads(payload)
        assert Path(native["cwd"]).is_dir()
        database = LocalDatabase(Path(os.environ["OH_GOON_DB"]), read_only=True)
        lease = ExecutionCoordinator(database).active_lease()
        assert lease is not None and lease.fleet_run_id == running and lease.custody is not None
        assert lease.custody.phase == "WORKER_OWNED"
        assert lease.custody.owner_pid != os.getpid()
        assert (
            SystemLeaseEnvironment().process_identity(lease.custody.owner_pid)
            == lease.custody.owner_identity
        )
        for pid in (lease.custody.owner_pid, native["pid"]):
            birth = observe_process_birth(pid)
            assert birth is not None
            pidfds.append(os.pidfd_open(pid))
            assert observe_process_birth(pid) == birth
        assert (
            SystemLeaseEnvironment().process_identity(lease.custody.owner_pid)
            == lease.custody.owner_identity
        )
        deadline = monotonic() + 5
        while True:
            with layout.diagnostics_log.open("rb") as stream:
                prior_log = stream.read(128 * 1024 + 1)
            assert len(prior_log) <= 128 * 1024
            if prior_log.endswith(b"\n"):
                records = [json.loads(line) for line in prior_log.splitlines()]
                worker_starts = {
                    row["fleet_run_id"]: row
                    for row in records
                    if row["source"] == "run_worker" and row["operation"] == "start_execution"
                }
                if {control.fleet_run_id, running}.issubset(worker_starts):
                    break
            assert monotonic() < deadline, "normal worker startup diagnostics did not reach JSONL"
            select.select([], [], [], 0.01)
        assert stat.S_IMODE(layout.diagnostics_log.stat().st_mode) == 0o600
        before = runtime.runs.events(running)
        ancestors = [native["pid"]]
        ancestor = native["pid"]
        for _ in range(8):
            status = Path(f"/proc/{ancestor}/status").read_text()
            ancestor = int(
                next(line.split()[1] for line in status.splitlines() if line.startswith("PPid:"))
            )
            if ancestor == lease.custody.owner_pid:
                break
            ancestors.append(ancestor)
        else:
            raise AssertionError("held application has no bounded claimed-worker ancestry")
        commands = [
            row
            for row in before.events
            if type(row) is ProcessStartedEvent
            and row.pid in ancestors
            and row.step_id == healthy_step.step_id
        ]
        assert len(commands) == 1
        command = commands[0]
        assert command.process_identity is not None
        command_birth = observe_process_birth(command.pid)
        assert command_birth is not None
        assert command.process_identity == f"{command_birth.boot_id}:{command_birth.start_ticks}"
        pidfds.append(os.pidfd_open(command.pid))
        assert observe_process_birth(command.pid) == command_birth
        process_facts = {
            "application_pid": native["pid"],
            "command_pid": command.pid,
            "command_birth": command_birth.to_payload(),
            "command_process_identity": command.process_identity,
            "physical_execution_id": command.physical_execution_id,
        }
        (root / "native-fault-boundary.json").write_text(json.dumps(process_facts, sort_keys=True))
        # Trace only current caller threads; no fork tracing can perturb worker
        # startup. The controlled diagnostic below runs on this caller thread.
        thread_ids = sorted(int(path.name) for path in Path(f"/proc/{os.getpid()}/task").iterdir())
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
            assert tracer.poll() is None and monotonic() < deadline, (
                "diagnostic trace did not attach"
            )
            select.select([], [], [], 0.01)
        filler_bytes = _fill_mount(mount)
        private_canary = "LOG_PRESSURE_PRIVATE_DIAGNOSTIC_CANARY"
        event = DiagnosticEvent(
            severity="INFO",
            source="storage_pressure_probe",
            owner="NativeStorageFaultFixture",
            operation="append_active_run_log",
            execution_affected=False,
            fleet_run_id=running,
            message=private_canary.ljust(MAX_DIAGNOSTIC_MESSAGE_CHARS, "x"),
        )
        runtime.diagnostics.emit(event)
        with (root / "native.stderr").open("rb") as stream:
            diagnostic_output = stream.read(128 * 1024 + 1)
        assert len(diagnostic_output) <= 128 * 1024
        fallbacks = [
            json.loads(line)
            for line in diagnostic_output.decode().splitlines()
            if line.startswith("{")
        ]
        matching = [
            row
            for row in fallbacks
            if row.get("event") == "DIAGNOSTIC_SINK_FAILED"
            and row.get("diagnostic_event_id") == event.event_id
        ]
        assert len(matching) == 1
        fallback = matching[0]
        assert fallback["exception_type"] == "OSError" and "[Errno 28]" in fallback["message"]
        assert private_canary not in json.dumps(fallback)
        with layout.diagnostics_log.open("rb") as stream:
            after_log = stream.read(128 * 1024 + 1)
        assert len(after_log) <= 128 * 1024 and after_log.startswith(prior_log)
        assert runtime.runs.get(running).run.status.value == "RUNNING"
        tracer.send_signal(signal.SIGINT)
        trace_exit = tracer.wait(timeout=5)
        assert trace_exit in (0, -signal.SIGINT)
        for tid in thread_ids:
            status = Path(f"/proc/{tid}/status").read_text()
            assert (
                int(
                    next(
                        line.split()[1]
                        for line in status.splitlines()
                        if line.startswith("TracerPid:")
                    )
                )
                == 0
            )
        with (root / "hold").open("wb") as channel:
            channel.write(b"x")
        terminal = runtime.runs.wait_for_terminal(running, timeout=60)
        step = terminal.game_tape.repositories[0].steps[0]
        assert (
            terminal.run.status.value == "COMPLETED" and terminal.outcome.value == expected_outcome
        )
        assert terminal.game_tape.status == expected_tape
        assert step.settlement.outcome == ("SUCCESS" if native_exit == 0 else "FAILED")
        assert step.exit_code == step.settlement.observed_returncode == native_exit
        assert step.settlement.cleanup == "VERIFIED" and len(step.attempts) == 1
        assert step.attempts[0].returncode == native_exit
        assert (
            step.attempts[0].supervisor is not None
            and step.attempts[0].supervisor.final_process_group_quiescent
        )
        assert step.project_source == healthy_step.project_source
        assert len(step.artifacts) == 1 and isinstance(step.artifacts[0], PresentPlanArtifact)
        assert step.artifacts[0].retained_object is not None
        assert (step.artifacts[0].sha256, step.artifacts[0].byte_count) == (
            artifact.sha256,
            artifact.byte_count,
        )
        assert not layout.run_workspaces.exists() or not list(layout.run_workspaces.iterdir())
        assert not list((layout.content_store / "staging").iterdir())
        assert not Path(native["cwd"]).exists()
        for descriptor in pidfds:
            assert select.select([descriptor], [], [], 5)[0], "original process did not retire"
        (mount / "owned-filler").unlink()
        resumed_event = DiagnosticEvent(
            severity="INFO",
            source="storage_pressure_probe",
            owner="NativeStorageFaultFixture",
            operation="append_after_capacity_restored",
            execution_affected=False,
            fleet_run_id=running,
            message="Diagnostic storage has capacity again",
        )
        runtime.diagnostics.emit(resumed_event)
        with layout.diagnostics_log.open("rb") as stream:
            resumed_log = stream.read(128 * 1024 + 1)
        assert len(resumed_log) <= 128 * 1024 and resumed_log.startswith(after_log)
        resume_payload = resumed_event.model_dump_json(exclude_none=True).encode("utf-8") + b"\n"
        assert resumed_log[len(after_log) :].endswith(resume_payload)
        valid_records: list[dict[str, Any]] = []
        invalid_lines = 0
        for line in resumed_log.splitlines():
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                invalid_lines += 1
            else:
                if isinstance(value, dict):
                    valid_records.append(value)
        resume_matches = [
            row for row in valid_records if row.get("event_id") == resumed_event.event_id
        ]
        resume_observation = {
            "resume_event_id": resumed_event.event_id,
            "bytes_before_resume": len(after_log),
            "bytes_after_resume": len(resumed_log),
            "complete_resume_payload_appended": True,
            "resume_records_on_valid_lines": len(resume_matches),
            "invalid_nonempty_lines": invalid_lines,
            "raw_last_line_utf8": resumed_log.splitlines()[-1].decode("utf-8"),
        }
        (root / "log-resume-observation.json").write_text(
            json.dumps(resume_observation, sort_keys=True)
        )
        assert len(resume_matches) == 1, resume_observation
        recorded_run = terminal.run.model_dump(mode="json")
        recorded_tape = terminal.game_tape.model_dump(mode="json")
        runtime.shutdown(wait_for_runs=True)
        runtime = ApplicationRuntime.build(standard_local=True)
        reopened = runtime.runs.get(running)
        assert reopened.run.model_dump(mode="json") == recorded_run
        assert reopened.game_tape.model_dump(mode="json") == recorded_tape
        assert _cli(root, "runs", "show", running)["outcome"] == expected_outcome
        public_step = _cli(root, "runs", "tape", running)["repositories"][0]["steps"][0]
        assert public_step["exit_code"] == native_exit
        assert public_step["settlement"]["cleanup"] == "VERIFIED"
        public_artifacts = _cli(root, "runs", "outputs", running)["repositories"][0]["steps"][0][
            "artifacts"
        ]
        assert len(public_artifacts) == 1 and public_artifacts[0]["status"] == "PRESENT"
        for run_id, observed_step in ((control.fleet_run_id, healthy_step), (running, step)):
            retained = observed_step.artifacts[0]
            assert isinstance(retained, PresentPlanArtifact)
            destination = root / f"{run_id}-export.bin"
            _cli(
                root,
                "runs",
                "export-artifact",
                run_id,
                "--step",
                observed_step.step_id,
                "--path",
                "model.bin",
                "--output",
                str(destination),
            )
            assert sha256_file_with_size(destination) == (retained.sha256, retained.byte_count)
        events = runtime.runs.events(running)
        assert (
            not events.has_more
            and sum(row.event_type.value == "RUN_COMPLETED" for row in events.events) == 1
        )
        report = {
            "boundary": f"diagnostics-{native_exit}",
            "mount": mount_facts,
            "fault_scope": "controlled diagnostic producer during a held claimed Run",
            "healthy_run": control.fleet_run_id,
            "run_id": running,
            "native_exit": native_exit,
            "run_outcome": expected_outcome,
            "step_outcome": step.settlement.outcome,
            "cleanup": step.settlement.cleanup,
            "diagnostic_writer_pid": os.getpid(),
            "native_processes": process_facts,
            "failed_diagnostic_event_id": event.event_id,
            "native_worker_start_event_id": worker_starts[running]["event_id"],
            "fallback": fallback,
            "filler_bytes": filler_bytes,
            "prior_valid_log_bytes": len(prior_log),
            "after_failed_append_bytes": len(after_log),
            "prior_valid_log_prefix_preserved": True,
            "resume_event_id": resumed_event.event_id,
            "resume_record_on_valid_separate_line": True,
            "failed_append_tail": "diagnostic loss; whole-file JSONL validity is not claimed",
            "previous_artifact_sha256": artifact.sha256,
            "project_source_sha256": healthy_step.project_source.content_sha256
            if healthy_step.project_source
            else None,
            "fresh_runtime_and_cli_readback": True,
        }
        (root / "result.json").write_text(json.dumps(report, sort_keys=True))
    finally:
        original_error = sys.exception()
        try:
            try:
                try:
                    if tracer is not None and tracer.poll() is None:
                        try:
                            tracer.send_signal(signal.SIGINT)
                            tracer.wait(timeout=5)
                        finally:
                            if tracer.poll() is None:
                                tracer.kill()
                                tracer.wait(timeout=5)
                finally:
                    try:
                        if (
                            running is not None
                            and not runtime.runs.get(running).run.status.terminal
                        ):
                            runtime.runs.cancel(running)
                    finally:
                        runtime.shutdown(wait_for_runs=True)
            finally:
                if ready_fd is not None:
                    os.close(ready_fd)
                for descriptor in pidfds:
                    os.close(descriptor)
                trace_errors.close()
                (mount / "owned-filler").unlink(missing_ok=True)
                subprocess.run(["umount", str(mount)], check=True)
        except BaseException as cleanup_error:
            if original_error is None:
                raise
            original_error.add_note(f"Owned log-pressure fixture cleanup failed: {cleanup_error}")


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
    _package(
        root,
        uv_preparation=boundary == "uv-preparation",
        diagnostic_exit=boundary.startswith("diagnostics-"),
    )
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
        / (
            "diagnostics/diagnostics.jsonl"
            if boundary.startswith("diagnostics-")
            else "run-workspaces"
            if boundary == "uv-preparation"
            else f"content-store/{boundary}"
        )
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
    if boundary.startswith("diagnostics-"):
        failures = [
            line
            for line in failures
            if re.match(rf"{result['diagnostic_writer_pid']}\s+write\(", line)
        ]
        assert failures, "no controlled diagnostic append returned real JSONL ENOSPC"
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


@pytest.mark.parametrize("native_exit", (0, 23))
def test_log_enospc_preserves_claimed_run_result(tmp_path: Path, native_exit: int) -> None:
    _run_fault_witness(tmp_path, f"diagnostics-{native_exit}")


if __name__ == "__main__":
    if sys.argv[2].startswith("diagnostics-"):
        _diagnostic_witness(Path(sys.argv[1]), int(sys.argv[2].partition("-")[2]))
    else:
        _witness(Path(sys.argv[1]), sys.argv[2])
