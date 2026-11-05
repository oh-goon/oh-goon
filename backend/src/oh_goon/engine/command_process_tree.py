from __future__ import annotations

import array
import ctypes
import os
import select
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Thread

from oh_goon.runtime import exec_guard_protocol
from oh_goon.runtime.process_group import process_group_exists, reap_process_group_children
from oh_goon.runtime.termination import ProcessTreeTerminationReceipt, TerminationRequest

_native_reaping_owner_pid: int | None = None


def native_child_reaping_supported() -> bool:
    if not sys.platform.startswith("linux"):
        return False
    try:
        observed = ctypes.c_int()
        libc = ctypes.CDLL(None, use_errno=True)
        return bool(libc.prctl(37, ctypes.byref(observed), 0, 0, 0) == 0)  # PR_GET_CHILD_SUBREAPER
    except (AttributeError, OSError):
        return False


def enable_native_child_reaping() -> None:
    """Use Linux's native subreaper owner, never a broad wait-for-any-child loop."""
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
        raise OSError(ctypes.get_errno(), "Native command child reaping is unavailable")
    global _native_reaping_owner_pid
    _native_reaping_owner_pid = os.getpid()


def owns_native_child_reaping() -> bool:
    return _native_reaping_owner_pid == os.getpid()


@dataclass(slots=True)
class CommandParentGuard:
    """One native command's passive group anchor and bounded kernel-handle lease."""

    channel: socket.socket
    child_channel: socket.socket
    guardian_pid: int | None = None
    guardian_pidfd: int | None = None
    group: int | None = None
    settled: bool = False
    cleanup_forced: bool = False

    @classmethod
    def create(cls) -> CommandParentGuard:
        enable_native_child_reaping()
        parent, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        parent.settimeout(exec_guard_protocol.STARTUP_TIMEOUT_SECONDS)
        return cls(parent, child)

    @property
    def child_descriptor(self) -> int:
        return self.child_channel.fileno()

    def accept(self, process: subprocess.Popen[str]) -> None:
        """Receive the guardian's own pidfd, never infer ownership from a PID string."""
        self.child_channel.close()
        received: list[int] = []
        try:
            data, ancillary, flags, _address = self.channel.recvmsg(
                64, socket.CMSG_SPACE(4 * array.array("i").itemsize), socket.MSG_CMSG_CLOEXEC
            )
            for level, kind, payload in ancillary:
                if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                    descriptors = array.array("i")
                    descriptors.frombytes(
                        payload[: len(payload) - len(payload) % descriptors.itemsize]
                    )
                    received.extend(descriptors)
            if flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC) or len(received) != 1:
                raise RuntimeError("Command parent-death guard readiness is incomplete")
            raw_pid = data.removeprefix(exec_guard_protocol.GUARD_READY)
            if not data.startswith(exec_guard_protocol.GUARD_READY) or not raw_pid.isdigit():
                raise RuntimeError("Command parent-death guard readiness is invalid")
            guardian = int(raw_pid)
            descriptor = received[0]
            with Path(f"/proc/self/fdinfo/{descriptor}").open("rb") as stream:
                info = stream.read(8192)
            kernel_pid = next(
                (
                    int(line.split(b":", 1)[1].strip())
                    for line in info.splitlines()
                    if line.startswith(b"Pid:")
                ),
                None,
            )
            if (
                guardian <= 0
                or guardian == process.pid
                or guardian != kernel_pid
                or os.getsid(guardian) != process.pid
                or os.getpgid(guardian) != guardian
                or select.select([descriptor], [], [], 0)[0]
            ):
                raise RuntimeError("Command parent-death guard kernel identity differs")
            os.set_inheritable(descriptor, False)
            self.guardian_pid = guardian
            self.guardian_pidfd = descriptor
            self.group = process.pid
            received.clear()
            self.channel.sendall(exec_guard_protocol.GUARD_START)
            if self.channel.recv(2) != exec_guard_protocol.GUARD_ACTIVE:
                raise RuntimeError("Command parent-death guard activation is incomplete")
        finally:
            for descriptor in received:
                os.close(descriptor)

    def require_live(self) -> None:
        if self.settled:
            return
        if self.guardian_pidfd is None or select.select([self.guardian_pidfd], [], [], 0)[0]:
            raise RuntimeError("Command parent-death group ownership was lost")

    def finish_quiescent(self) -> bool:
        """Retire the helper only after the original owner proves real work absent."""
        if self.settled:
            return False
        if self.group is None or _process_group_exists(self.group, parent_guard=self):
            raise RuntimeError("Command group is not quiescent before guard retirement")
        descriptor = self.guardian_pidfd
        forced = False
        if descriptor is not None and not select.select([descriptor], [], [], 0)[0]:
            # The live guardian pins this session/PG number while the kernel
            # reaps only adopted children of the original owned workload group.
            reap_process_group_children(self.group)
            try:
                self.channel.sendall(exec_guard_protocol.GUARD_QUIESCENT)
            except OSError:
                pass
            if not select.select([descriptor], [], [], 1)[0]:
                # Only the metadata helper remains; this exact pidfd cannot
                # target a reused PID. The workload result stays unchanged.
                signal.pidfd_send_signal(descriptor, signal.SIGKILL)
                forced = True
                self.cleanup_forced = True
                if not select.select([descriptor], [], [], 1)[0]:
                    raise RuntimeError("Command parent-death guard did not retire")
        self.settled = True
        if descriptor is not None:
            try:
                os.waitid(os.P_PIDFD, descriptor, os.WEXITED)
            except ChildProcessError:
                # The same group-scoped native reaper may already have retired
                # this helper. Its pidfd still proved the exact process dead.
                pass
        self.close()
        return forced

    def close(self) -> None:
        self.child_channel.close()
        self.channel.close()
        if self.guardian_pidfd is not None:
            descriptor, self.guardian_pidfd = self.guardian_pidfd, None
            os.close(descriptor)


def _process_group_exists(
    process_group_id: int, *, parent_guard: CommandParentGuard | None = None
) -> bool:
    if parent_guard is not None:
        if parent_guard.settled:
            return False
    return process_group_exists(process_group_id)


POLL_INTERVAL_SECONDS = 0.1
TERMINATION_GRACE_SECONDS = 1.0
OUTPUT_DRAIN_TIMEOUT_SECONDS = 5.0


def _finish_process_output(
    process: subprocess.Popen[str],
    readers: tuple[Thread, Thread],
    *,
    parent_guard: CommandParentGuard | None = None,
) -> bool:
    forced_termination = False
    if os.name != "nt" and _process_group_exists(process.pid, parent_guard=parent_guard):
        # The command leader exited while descendants still own its process group/pipes.
        # A Step must not leave background work behind after its command returns.
        forced_termination = (
            _terminate_process_group(process, close_pipes=False, parent_guard=parent_guard)
            or forced_termination
        )
    if _join_readers(readers, timeout=OUTPUT_DRAIN_TIMEOUT_SECONDS):
        if parent_guard is not None:
            forced_termination = parent_guard.finish_quiescent() or forced_termination
        return forced_termination
    forced_termination = (
        _terminate_process_group(process, parent_guard=parent_guard) or forced_termination
    )
    _close_process_pipes(process)
    if not _join_readers(readers, timeout=TERMINATION_GRACE_SECONDS):
        raise RuntimeError("Command output readers did not stop after process-group cleanup")
    if parent_guard is not None:
        forced_termination = parent_guard.finish_quiescent() or forced_termination
    return forced_termination


def _join_readers(readers: tuple[Thread, Thread], *, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    for reader in readers:
        reader.join(timeout=max(0.0, deadline - time.monotonic()))
    return all(not reader.is_alive() for reader in readers)


def _close_process_pipes(process: subprocess.Popen[str]) -> None:
    for pipe in (process.stdin, process.stdout, process.stderr):
        if pipe is not None and not pipe.closed:
            pipe.close()


def terminate_owned_process_tree(
    process: subprocess.Popen[str],
    request: TerminationRequest,
    *,
    parent_guard: CommandParentGuard | None = None,
) -> ProcessTreeTerminationReceipt:
    """Apply one bounded TERM -> grace -> KILL authority and prove final quiescence.

    The caller supplies the admitted execution identity/generation in ``request``.
    This owner acts only on the already-owned process group represented by ``process``.
    """
    escalated = _terminate_process_group(
        process, close_pipes=True, grace_seconds=request.grace_seconds, parent_guard=parent_guard
    )
    quiescent = os.name == "nt" or not _process_group_exists(process.pid, parent_guard=parent_guard)
    if quiescent and parent_guard is not None:
        escalated = parent_guard.finish_quiescent() or escalated
    return ProcessTreeTerminationReceipt(
        request_identity=request.request_identity,
        term_requested=True,
        grace_seconds=request.grace_seconds,
        escalated_to_kill=escalated,
        quiescent=quiescent,
        final_state="quiescent" if quiescent else "survivors-detected",
    )


def _terminate_process_group(
    process: subprocess.Popen[str],
    *,
    close_pipes: bool = True,
    grace_seconds: float = TERMINATION_GRACE_SECONDS,
    parent_guard: CommandParentGuard | None = None,
) -> bool:
    forced = (
        _terminate_windows(process, grace_seconds)
        if os.name == "nt"
        else _terminate_posix(process, grace_seconds, parent_guard=parent_guard)
    )
    process.wait()
    if close_pipes:
        _close_process_pipes(process)
    return forced


def _terminate_windows(process: subprocess.Popen[str], grace_seconds: float) -> bool:
    if process.poll() is not None:
        return False
    process.terminate()
    try:
        process.wait(timeout=grace_seconds)
        return False
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        return True


def _terminate_posix(
    process: subprocess.Popen[str],
    grace_seconds: float,
    *,
    parent_guard: CommandParentGuard | None = None,
) -> bool:
    group = process.pid
    if not _process_group_exists(group, parent_guard=parent_guard):
        return False
    try:
        os.killpg(group, signal.SIGTERM)
    except ProcessLookupError:
        return False
    deadline = time.monotonic() + grace_seconds
    while _process_group_exists(group, parent_guard=parent_guard) and time.monotonic() < deadline:
        # Reap our leader during grace: the portable group probe also sees zombies.
        # Its exit alone does not settle descendants, so keep probing the whole group.
        process.poll()
        time.sleep(POLL_INTERVAL_SECONDS)
    process.poll()
    if not _process_group_exists(group, parent_guard=parent_guard):
        return False
    try:
        os.killpg(group, signal.SIGKILL)
        return True
    except ProcessLookupError:
        return False
