"""Selected Linux namespace custody, separate from strict/foreign-writer proof."""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.execution_materialization import (
    ExecutionMaterialization,
    execution_materialization_fingerprint,
)
from oh_goon.core.models.fleet_plan import ResolvedCommandStep
from oh_goon.core.time import utc_now
from oh_goon.engine.command import (
    CommandCancelledError,
    CommandProcessLifecycleEvent,
    CommandTimeoutExpired,
    observe_command_processes,
)
from oh_goon.integrations import fleet_plan_bubblewrap_isolation as backend_module
from oh_goon.integrations._fleet_plan_step_outcome import from_command, step_succeeded
from oh_goon.integrations.fleet_plan_bubblewrap_isolation import BubblewrapProcessIsolation
from oh_goon.integrations.fleet_plan_bubblewrap_lifetime import BubblewrapWriterNamespace
from oh_goon.integrations.fleet_plan_process_isolation import StandardIsolatedProcessRequest


def _materialization(workspace: Path) -> ExecutionMaterialization:
    metadata = workspace.stat()
    value = ExecutionMaterialization.model_construct(
        materialization_id="materialization-1",
        attempt_id="operation-1",
        assignment_id="assignment-1",
        assignment_fingerprint="assignment-fingerprint",
        workspace_id="workspace-1",
        input_checkpoint_id="checkpoint-1",
        lease_id="lease-1",
        lease_generation=1,
        executor_id="original-worker",
        executor_generation=1,
        created_at=utc_now(),
        writer_scope_kind="native_namespace_scope",
        workspace_device=metadata.st_dev,
        workspace_inode=metadata.st_ino,
        workspace_mode=metadata.st_mode,
        resolved_fingerprint="pending",
    )
    return ExecutionMaterialization.model_validate(
        {**value.model_dump(), "resolved_fingerprint": execution_materialization_fingerprint(value)}
    )


@contextmanager
def _request(backend, workspace, *, body, verify=None):
    descriptor = os.open(workspace, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        yield StandardIsolatedProcessRequest(
            workspace=workspace,
            cwd=workspace,
            argv=("/usr/bin/python3", "-I", "-c", body),
            env={"PATH": os.defpath},
            backend_configuration_id=backend.configuration_identity(),
            require_workspace_current=verify or (lambda: None),
            workspace_materialization=_materialization(workspace),
            workspace_directory_fd=descriptor,
        )
    finally:
        os.close(descriptor)


def _backend():
    binary = shutil.which("bwrap")
    if sys.platform != "linux" or binary is None or not hasattr(os, "pidfd_open"):
        pytest.skip("selected Linux Bubblewrap/pidfd environment is unavailable")
    return BubblewrapProcessIsolation(binary)


@pytest.mark.process
def test_native_scope_edits_only_candidate_and_closes_inherited_directory_fds(tmp_path):
    backend = _backend()
    workspace, developer, other = (tmp_path / name for name in ("candidate", "developer", "other"))
    for directory in (workspace, developer, other):
        directory.mkdir()
        (directory / "code.py").write_text("ORIGINAL\n")
    (developer / ".git").mkdir()
    (developer / ".git" / "config").write_text("AUTHORIZATION_CREDENTIAL_CANARY\n")
    git_marker = "gitdir: /unexposed/common/worktrees/candidate\nCREDENTIAL_CANARY\n"
    (workspace / ".git").write_text(git_marker)
    body = f"""
import json,os,pathlib,stat
path=pathlib.Path('code.py')
path.write_text('CHANGED\\n');assert path.read_text()=='CHANGED\\n'
path.write_text('ORIGINAL\\n')
for metadata in (pathlib.Path('/workspace/.git'),pathlib.Path.cwd()/'.git'):
    assert metadata.read_text()==''
    try: metadata.write_text('gitdir: /unexposed/foreign')
    except OSError: pass
    else: raise AssertionError('native Git metadata mask was writable')
    try: (metadata/'index').write_text('UNAUTHORIZED')
    except OSError: pass
    else: raise AssertionError('native Git index became writable')
try: pathlib.Path({str(developer / ".git" / "config")!r}).read_text()
except OSError: pass
else: raise AssertionError('unexposed developer credentials became readable')
for outside in ({str(developer / "code.py")!r},{str(other / "code.py")!r}):
    try: pathlib.Path(outside).write_text('UNAUTHORIZED')
    except OSError: pass
    else: raise AssertionError('another workspace was writable')
inherited=[]
for raw in os.listdir('/proc/self/fd'):
    try:
        fd=int(raw)
        if fd>2 and stat.S_ISDIR(os.fstat(fd).st_mode): inherited.append(fd)
    except OSError: pass
assert not inherited, inherited
print(json.dumps({{'edited':True,'inherited_directory_fds':inherited}}))
"""
    events = []
    with (
        _request(backend, workspace, body=body) as request,
        observe_command_processes(events.append),
    ):
        result = backend.execute(request, timeout=10, cancel_requested=None)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"edited": True, "inherited_directory_fds": []}
    scope = result.standard_isolation.workspace_writer_scope
    assert scope is not None and scope.materialization == request.workspace_materialization
    assert scope.physical_execution_id == result.physical_execution_id
    assert scope.backend_configuration_id == request.backend_configuration_id
    original = os.stat("/proc/self/ns/pid")
    assert (scope.namespace_device, scope.namespace_inode) != (original.st_dev, original.st_ino)
    assert scope.namespace_distinct and scope.init_death_observed
    assert (scope.workspace_device, scope.workspace_inode, scope.workspace_mode) == (
        workspace.stat().st_dev,
        workspace.stat().st_ino,
        workspace.stat().st_mode,
    )
    assert [event.kind for event in events].count("PROCESS_STARTING") == 1
    assert [
        event.workspace_materialization_id for event in events if event.kind == "PROCESS_STARTING"
    ] == [scope.materialization.materialization_id]
    assert all(event.physical_execution_id == result.physical_execution_id for event in events)
    assert all(
        (directory / "code.py").read_text() == "ORIGINAL\n"
        for directory in (workspace, developer, other)
    )
    assert (workspace / ".git").read_text() == git_marker
    assert (developer / ".git" / "config").read_text() == "AUTHORIZATION_CREDENTIAL_CANARY\n"


@pytest.mark.process
def test_read_only_candidate_probe_has_no_writer_birth_stamp(tmp_path):
    backend = _backend()
    workspace = tmp_path / "candidate"
    workspace.mkdir()
    (workspace / "code.py").write_text("ORIGINAL\n")
    body = """
import pathlib
try: pathlib.Path('code.py').write_text('UNAUTHORIZED')
except OSError: pass
else: raise AssertionError('read-only probe changed candidate')
"""
    events = []
    with (
        _request(backend, workspace, body=body) as request,
        observe_command_processes(events.append),
    ):
        result = backend.execute(
            replace(request, read_only_workspace=True), timeout=10, cancel_requested=None
        )
    assert result.returncode == 0, result.stderr
    assert result.standard_isolation.workspace_read_only is True
    births = [event for event in events if event.kind == "PROCESS_STARTING"]
    assert len(births) == 1 and births[0].workspace_materialization_id is None
    assert (workspace / "code.py").read_text() == "ORIGINAL\n"


@pytest.mark.process
@pytest.mark.parametrize("finish", ["completion", "cancel", "deadline"])
def test_native_closed_scope_stops_late_setsid_writer(tmp_path, finish):
    backend = _backend()
    workspace = tmp_path / "candidate"
    workspace.mkdir()
    body = f"""
import os,pathlib,signal,time
if os.fork()==0:
    os.setsid();signal.signal(signal.SIGTERM,signal.SIG_IGN)
    while True:
        with pathlib.Path('progress').open('a') as stream: stream.write('write\\n')
        time.sleep(.01)
else:
    while not pathlib.Path('progress').exists(): time.sleep(.001)
    if {finish!r}!='completion':
        while True: time.sleep(1)
"""
    with _request(backend, workspace, body=body) as request:
        if finish == "completion":
            result = backend.execute(request, timeout=10, cancel_requested=None)
            assert result.returncode == 0, result.stderr
            isolation, physical = result.standard_isolation, result.physical_execution_id
        else:
            expected = CommandCancelledError if finish == "cancel" else CommandTimeoutExpired
            with pytest.raises(expected) as failure:
                backend.execute(
                    request,
                    timeout=1 if finish == "deadline" else 10,
                    cancel_requested=(lambda: (workspace / "progress").exists())
                    if finish == "cancel"
                    else None,
                )
            isolation, physical = (
                failure.value.standard_isolation,
                failure.value.physical_execution_id,
            )
    assert isolation is not None and isolation.workspace_writer_scope is not None
    assert isolation.workspace_writer_scope.init_death_observed
    assert isolation.workspace_writer_scope.physical_execution_id == physical
    completed = (workspace / "progress").read_bytes()
    time.sleep(0.15)
    assert completed and (workspace / "progress").read_bytes() == completed


@pytest.mark.process
def test_missing_exec_ack_does_not_turn_closed_namespace_into_step_success(tmp_path, monkeypatch):
    backend = _backend()
    workspace = tmp_path / "candidate"
    workspace.mkdir()
    monkeypatch.setattr(backend_module, "_standard_workload_exec_completed", lambda *_: False)
    with _request(backend, workspace, body="raise SystemExit(7)") as request:
        result = backend.execute(request, timeout=10, cancel_requested=None)
    assert result.returncode == 7
    assert result.standard_isolation.preflight == "not_recorded"
    assert result.standard_isolation.workspace_writer_scope.init_death_observed
    assert not step_succeeded(
        ResolvedCommandStep(step_id="verify", argv=("verify",)), from_command(result)
    )


@pytest.mark.process
def test_zero_without_exec_ack_remains_unverified(tmp_path, monkeypatch):
    backend = _backend()
    workspace = tmp_path / "candidate"
    workspace.mkdir()
    monkeypatch.setattr(backend_module, "_standard_workload_exec_completed", lambda *_: False)
    with _request(backend, workspace, body="pass") as request:
        with pytest.raises(FleetPlanInvalid, match="without native workload-exec evidence"):
            backend.execute(request, timeout=10, cancel_requested=None)


def test_forged_native_info_cannot_kill_a_foreign_process(tmp_path, monkeypatch):
    if sys.platform != "linux":
        pytest.skip("Linux native process observation required")
    workspace = tmp_path / "candidate"
    workspace.mkdir()
    signals = []
    monkeypatch.setattr(
        "oh_goon.integrations.fleet_plan_bubblewrap_lifetime.signal.pidfd_send_signal",
        lambda *args: signals.append(args),
    )
    descriptor = os.open(workspace, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        with BubblewrapWriterNamespace(
            _materialization(workspace), "backend", lambda: None, descriptor
        ) as namespace:
            namespace.observe(
                CommandProcessLifecycleEvent(
                    kind="PROCESS_STARTING", physical_execution_id="physical"
                )
            )
            os.write(namespace.info_writer, json.dumps({"child-pid": os.getpid()}).encode())
            with pytest.raises(FleetPlanInvalid, match="distinct namespace init"):
                namespace.observe(
                    CommandProcessLifecycleEvent(
                        kind="PROCESS_STARTED", physical_execution_id="physical", pid=os.getppid()
                    )
                )
            assert namespace.evidence("other-physical") is None
    finally:
        os.close(descriptor)
    assert not signals


def test_candidate_binding_rejects_a_foreign_directory_handle_before_launch(tmp_path):
    backend = _backend()
    workspace, other = tmp_path / "candidate", tmp_path / "other"
    workspace.mkdir()
    other.mkdir()
    descriptor = os.open(other, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    events = []
    try:
        request = StandardIsolatedProcessRequest(
            workspace=workspace,
            cwd=workspace,
            argv=("/usr/bin/python3", "-c", "open('started','w').write('work')"),
            env={"PATH": os.defpath},
            backend_configuration_id=backend.configuration_identity(),
            require_workspace_current=lambda: None,
            workspace_materialization=_materialization(workspace),
            workspace_directory_fd=descriptor,
        )
        with observe_command_processes(events.append):
            with pytest.raises(FleetPlanInvalid, match="original materialization"):
                backend.execute(request, timeout=10, cancel_requested=None)
        assert os.fstat(descriptor).st_ino == other.stat().st_ino
    finally:
        os.close(descriptor)
    assert not events and not (workspace / "started").exists() and not (other / "started").exists()


def test_materialization_requires_the_original_handle_in_its_request(tmp_path):
    backend = _backend()
    workspace = tmp_path / "candidate"
    workspace.mkdir()
    with pytest.raises(FleetPlanInvalid, match="original directory handle"):
        StandardIsolatedProcessRequest(
            workspace=workspace,
            cwd=workspace,
            argv=("/usr/bin/true",),
            env={},
            backend_configuration_id=backend.configuration_identity(),
            require_workspace_current=lambda: None,
            workspace_materialization=_materialization(workspace),
        )
