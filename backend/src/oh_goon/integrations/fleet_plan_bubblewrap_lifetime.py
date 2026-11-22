"""Native namespace handles for one original Bubblewrap candidate launch."""

from __future__ import annotations

import json
import os
import select
import signal
import stat
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.execution_materialization import ExecutionMaterialization
from oh_goon.core.models.fleet_plan_evidence import PlanWorkspaceWriterScopeEvidence
from oh_goon.core.process_birth import ProcessBirthIdentity, observe_process_birth
from oh_goon.core.time import utc_now
from oh_goon.engine.command import CommandProcessLifecycleEvent


def _bounded_proc(path: Path, limit: int) -> str:
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise FleetPlanInvalid("Candidate namespace observation exceeds its bound")
    return raw.decode("ascii")


def _descends_from(pid: int, original: int) -> bool:
    """Follow only the reported child's bounded native parent chain, not a PID census."""

    visited: set[int] = set()
    for _ in range(16):
        if pid == original:
            return True
        if pid <= 1 or pid in visited:
            return False
        visited.add(pid)
        parents = [
            line.partition(":")[2].strip()
            for line in _bounded_proc(Path(f"/proc/{pid}/status"), 65536).splitlines()
            if line.startswith("PPid:")
        ]
        if len(parents) != 1 or not parents[0].isdecimal():
            return False
        pid = int(parents[0])
    return False


@dataclass(slots=True)
class BubblewrapWriterNamespace:
    """Gate workload birth on exact native PID-namespace observations.

    Handles stay in the original backend; only final immutable observations
    enter execution evidence. No PID-only lookup can later recreate this lease.
    """

    materialization: ExecutionMaterialization
    backend_configuration_id: str
    require_workspace_current: Callable[[], None]
    workspace_directory_fd: int
    startup_timeout: float = 5.0
    info_reader: int = field(default=-1, init=False)
    info_writer: int = field(default=-1, init=False)
    block_reader: int = field(default=-1, init=False)
    block_writer: int = field(default=-1, init=False)
    workspace_fd: int = field(default=-1, init=False)
    workspace_alias_fd: int = field(default=-1, init=False)
    pidfd: int = field(default=-1, init=False)
    namespace_fd: int = field(default=-1, init=False)
    physical_execution_id: str | None = field(default=None, init=False)
    init_process: ProcessBirthIdentity | None = field(default=None, init=False)
    namespace_identity: tuple[int, int] | None = field(default=None, init=False)
    released: bool = field(default=False, init=False)
    owned_namespace: bool = field(default=False, init=False)

    def __enter__(self) -> BubblewrapWriterNamespace:
        if (
            sys.platform != "linux"
            or not hasattr(os, "pidfd_open")
            or not hasattr(signal, "pidfd_send_signal")
            or self.materialization.writer_scope_kind != "native_namespace_scope"
        ):
            raise FleetPlanInvalid("Candidate writing requires native Linux namespace custody")
        try:
            self.require_workspace_current()
            self.workspace_fd = os.dup(self.workspace_directory_fd)
            self.workspace_alias_fd = os.dup(self.workspace_directory_fd)
            for descriptor in (self.workspace_fd, self.workspace_alias_fd):
                os.set_inheritable(descriptor, False)
            info = os.fstat(self.workspace_fd)
            if not stat.S_ISDIR(info.st_mode) or (info.st_dev, info.st_ino, info.st_mode) != (
                self.materialization.workspace_device,
                self.materialization.workspace_inode,
                self.materialization.workspace_mode,
            ):
                raise FleetPlanInvalid("Candidate directory differs from original materialization")
            self.info_reader, self.info_writer = os.pipe2(os.O_CLOEXEC)
            self.block_reader, self.block_writer = os.pipe2(os.O_CLOEXEC)
        except BaseException:
            self.close()
            raise
        return self

    @property
    def arguments(self) -> tuple[str, ...]:
        return ("--info-fd", str(self.info_writer), "--block-fd", str(self.block_reader))

    @property
    def pass_fds(self) -> tuple[int, ...]:
        return (self.info_writer, self.block_reader, self.workspace_fd, self.workspace_alias_fd)

    def observe(self, event: CommandProcessLifecycleEvent) -> None:
        if event.kind == "PROCESS_STARTING":
            if self.physical_execution_id is not None:
                raise FleetPlanInvalid("Candidate namespace cannot adopt another native launch")
            self.physical_execution_id = event.physical_execution_id
            return
        if event.kind != "PROCESS_STARTED":
            return
        if event.physical_execution_id != self.physical_execution_id or event.pid is None:
            raise FleetPlanInvalid("Candidate namespace launch identity differs")
        self._close("info_writer")
        self._close("block_reader")
        report = self._read_native_info()
        pid = report.get("child-pid")
        if type(pid) is not int or pid <= 0 or pid == event.pid:
            raise FleetPlanInvalid("Native Bubblewrap namespace init is unavailable")
        original_birth = observe_process_birth(event.pid)
        if original_birth is None or not _descends_from(pid, event.pid):
            raise FleetPlanInvalid("Candidate namespace is outside the original native launch")
        self.pidfd = os.pidfd_open(pid)
        os.set_inheritable(self.pidfd, False)
        if select.select([self.pidfd], [], [], 0)[0]:
            raise FleetPlanInvalid("Candidate namespace init exited before admission")
        descriptor_pid = [
            line.partition(":")[2].strip()
            for line in _bounded_proc(Path(f"/proc/self/fdinfo/{self.pidfd}"), 8192).splitlines()
            if line.startswith("Pid:")
        ]
        if descriptor_pid != [str(pid)]:
            raise FleetPlanInvalid("Candidate namespace pidfd identifies another process")
        birth = observe_process_birth(pid)
        if birth is None:
            raise FleetPlanInvalid("Candidate namespace process birth is unproven")
        nspid = [
            line.partition(":")[2].split()
            for line in _bounded_proc(Path(f"/proc/{pid}/status"), 65536).splitlines()
            if line.startswith("NSpid:")
        ]
        if (
            len(nspid) != 1
            or len(nspid[0]) < 2
            or nspid[0][0] != str(pid)
            or nspid[0][-1] != "1"
            or any(not part.isdecimal() or int(part) <= 0 for part in nspid[0])
        ):
            raise FleetPlanInvalid("Candidate writer is not a distinct namespace init")
        self.namespace_fd = os.open(f"/proc/{pid}/ns/pid", os.O_RDONLY | os.O_CLOEXEC)
        namespace = os.fstat(self.namespace_fd)
        original = os.stat("/proc/self/ns/pid")
        identity = (namespace.st_dev, namespace.st_ino)
        if identity == (original.st_dev, original.st_ino):
            raise FleetPlanInvalid("Candidate writer retained the original PID namespace")
        if observe_process_birth(event.pid) != original_birth:
            raise FleetPlanInvalid("Original native launcher changed during namespace observation")
        self.owned_namespace = True
        self.require_workspace_current()
        current = os.stat(f"/proc/{pid}/ns/pid")
        if (
            observe_process_birth(pid) != birth
            or select.select([self.pidfd], [], [], 0)[0]
            or identity != (current.st_dev, current.st_ino)
        ):
            raise FleetPlanInvalid("Candidate namespace changed before workload release")
        self.init_process, self.namespace_identity = birth, identity
        if os.write(self.block_writer, b"R") != 1:
            raise FleetPlanInvalid("Candidate namespace release was not confirmed")
        self.released = True
        self._close("block_writer")

    def _read_native_info(self) -> dict[str, object]:
        deadline = time.monotonic() + self.startup_timeout
        data = bytearray()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([self.info_reader], [], [], remaining)[0]:
                raise FleetPlanInvalid("Native Bubblewrap namespace handshake timed out")
            part = os.read(self.info_reader, 8193 - len(data))
            if not part:
                raise FleetPlanInvalid("Native Bubblewrap namespace handshake ended early")
            data.extend(part)
            if len(data) > 8192:
                raise FleetPlanInvalid("Native Bubblewrap namespace handshake exceeds its bound")
            try:
                value = json.loads(data)
            except (ValueError, UnicodeError):
                continue
            if not isinstance(value, dict):
                raise FleetPlanInvalid("Native Bubblewrap namespace handshake is invalid")
            return value

    def evidence(
        self, physical_execution_id: str | None
    ) -> PlanWorkspaceWriterScopeEvidence | None:
        if (
            not self.released
            or physical_execution_id != self.physical_execution_id
            or physical_execution_id is None
            or self.pidfd < 0
            or self.init_process is None
            or self.namespace_identity is None
            or not select.select([self.pidfd], [], [], 1.0)[0]
        ):
            return None
        return PlanWorkspaceWriterScopeEvidence(
            materialization=self.materialization,
            physical_execution_id=physical_execution_id,
            backend_configuration_id=self.backend_configuration_id,
            namespace_device=self.namespace_identity[0],
            namespace_inode=self.namespace_identity[1],
            workspace_device=os.fstat(self.workspace_fd).st_dev,
            workspace_inode=os.fstat(self.workspace_fd).st_ino,
            workspace_mode=os.fstat(self.workspace_fd).st_mode,
            init_process=self.init_process,
            observed_at=utc_now(),
        )

    def _close(self, name: str) -> None:
        descriptor = getattr(self, name)
        if descriptor >= 0:
            setattr(self, name, -1)
            os.close(descriptor)

    def close(self) -> None:
        failure: OSError | None = None
        try:
            if (
                self.owned_namespace
                and self.pidfd >= 0
                and not select.select([self.pidfd], [], [], 0)[0]
            ):
                signal.pidfd_send_signal(self.pidfd, signal.SIGKILL)
                if not select.select([self.pidfd], [], [], 1.0)[0]:
                    raise OSError("Candidate namespace cleanup remains unproven")
        except OSError as error:
            failure = error
        for name in (
            "block_writer",
            "block_reader",
            "info_writer",
            "info_reader",
            "namespace_fd",
            "pidfd",
            "workspace_fd",
            "workspace_alias_fd",
        ):
            try:
                self._close(name)
            except OSError as error:
                if failure is None:
                    failure = error
                else:
                    failure.add_note("Another candidate namespace handle failed to close")
        if failure is not None:
            raise failure

    def __exit__(self, kind: object, error: BaseException | None, traceback: object) -> None:
        try:
            self.close()
        except (OSError, FleetPlanInvalid) as cleanup:
            if error is None:
                raise
            error.add_note(f"Candidate namespace cleanup failed: {type(cleanup).__name__}")
