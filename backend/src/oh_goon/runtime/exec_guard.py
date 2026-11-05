"""Thread-safe POSIX exec guard for owned child processes.

`subprocess.Popen(preexec_fn=...)` is unsafe when the parent has multiple threads:
the forked child may inherit a locked runtime state and deadlock before `exec`.  Oh-Goon's
batch runners intentionally launch subprocesses from worker threads, so process-session
and Linux parent-death setup belongs in this tiny post-exec wrapper instead.

The spawning command owner establishes the new session before supervision begins. The
guard retains an idempotent direct-invocation fallback, asks Linux to deliver SIGTERM if
the immediate parent dies, then atomically replaces itself with the requested command.  The real command
therefore keeps the guard PID/process-group identity without running Python callbacks in
the vulnerable fork-before-exec window.
"""

from __future__ import annotations

import array
import ctypes
import os
import resource
import select
import signal
import socket
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING or __package__:
    from oh_goon.runtime import exec_guard_protocol
else:  # Direct `python -S path/to/exec_guard.py` launch used by the command owner.
    import importlib

    exec_guard_protocol = importlib.import_module("exec_guard_protocol")


def _set_parent_death_signal(*, expected_parent_pid: int | None) -> None:
    if not sys.platform.startswith("linux"):
        return
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(1, signal.SIGTERM) != 0:  # PR_SET_PDEATHSIG
            raise OSError(ctypes.get_errno(), "prctl(PR_SET_PDEATHSIG) failed")
        # PR_SET_PDEATHSIG is not retroactive. If the spawning parent disappeared
        # before prctl was installed, fail closed instead of orphaning admitted work.
        if expected_parent_pid is not None and os.getppid() != expected_parent_pid:
            os.kill(os.getpid(), signal.SIGTERM)
    except (AttributeError, OSError):
        # Parent-death signalling is a Linux hardening aid. Process-group ownership
        # remains authoritative on other POSIX hosts or kernels without prctl.
        return


def _ensure_owned_session() -> None:
    """Retain direct-invocation safety without racing the normal spawn owner."""

    if os.getsid(0) != os.getpid():
        os.setsid()


def _attach_controlling_terminal() -> None:
    """Acquire only the private slave already wired by the command owner."""
    requested = os.environ.pop(exec_guard_protocol.CONTROLLING_TERMINAL_ENV, None)
    if requested is None:
        return
    if requested != "1" or not os.isatty(0):
        raise SystemExit("exec guard controlling terminal is invalid")
    import fcntl
    import termios

    fcntl.ioctl(0, termios.TIOCSCTTY, 0)
    os.tcsetpgrp(0, os.getpgrp())


def _close_foreign_descriptors(keep: tuple[int, ...], *, for_exec: bool = True) -> None:
    """A clean-exec guardian retains only its explicitly owned kernel handles."""
    maximum = resource.getrlimit(resource.RLIMIT_NOFILE)[0]
    if maximum == resource.RLIM_INFINITY:
        maximum = os.sysconf("SC_OPEN_MAX")
    start = 0
    for descriptor in sorted(keep):
        os.closerange(start, descriptor)
        os.set_inheritable(descriptor, for_exec)
        start = descriptor + 1
    os.closerange(start, int(maximum))


def _watch_command_owner(arguments: list[str]) -> int:
    """Pin this owned group; act only after positive original-owner death.

    Normal cleanup stays with the original command owner, which sends Q only
    after proving all workload members quiescent. The helper's own pidfd is
    transferred over the private socket; the workload never inherits that lease.
    """
    if len(arguments) != 4 or arguments[3] not in {"0", "1"}:
        return 125
    owner, channel_descriptor, ready = (int(value) for value in arguments[:3])
    kill_on_owner_death = arguments[3] == "1"
    # Native interpreter startup may open its own executable descriptor. It is
    # not needed by this kernel-only helper after the clean exec has completed.
    _close_foreign_descriptors((owner, channel_descriptor, ready), for_exec=False)
    channel = socket.socket(fileno=channel_descriptor)
    # Keep the original session identity allocated without becoming executable
    # workload-group membership. Its session ID is the original command PGID.
    group = os.getsid(0)
    os.setpgid(0, 0)
    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP, signal.SIGQUIT):
        signal.signal(signum, signal.SIG_IGN)
    own_pidfd = os.pidfd_open(os.getpid())
    try:
        if select.select([owner], [], [], 0)[0]:
            if kill_on_owner_death:
                os.killpg(group, signal.SIGKILL)
            return 125
        channel.sendmsg(
            [exec_guard_protocol.GUARD_READY + str(os.getpid()).encode("ascii")],
            [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", [own_pidfd]))],
        )
        os.close(own_pidfd)
        own_pidfd = -1
        readable, _, _ = select.select([owner, channel], [], [])
        if owner in readable:
            if kill_on_owner_death:
                os.killpg(group, signal.SIGKILL)
            return 125
        if channel.recv(2) != exec_guard_protocol.GUARD_START:
            return 125
        os.write(ready, exec_guard_protocol.GUARD_START)
        os.close(ready)
        ready = -1
        channel.sendall(exec_guard_protocol.GUARD_ACTIVE)
        peer_open = True
        while True:
            readable, _, _ = select.select([owner, channel] if peer_open else [owner], [], [])
            if owner in readable:
                if kill_on_owner_death:
                    try:
                        os.killpg(group, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                return 125
            if peer_open and channel in readable:
                packet = channel.recv(2)
                if packet == exec_guard_protocol.GUARD_QUIESCENT:
                    return 0
                if not packet:
                    # Losing a diagnostic/control pipe is not positive work
                    # quiescence. Retain the group anchor until owner death.
                    peer_open = False
    finally:
        if ready >= 0:
            os.close(ready)
        if own_pidfd >= 0:
            os.close(own_pidfd)
        os.close(owner)
        channel.close()


def _start_parent_guard(expected_parent_pid: int, *, kill_on_owner_death: bool) -> None:
    raw_channel = os.environ.pop(exec_guard_protocol.GUARD_CHANNEL_ENV, None)
    if not sys.platform.startswith("linux"):
        if raw_channel is not None:
            raise SystemExit("parent-death group guard requires Linux pidfds")
        return
    if raw_channel is None:
        raise SystemExit("parent-death group guard channel is missing")
    channel = int(raw_channel)
    if os.getppid() != expected_parent_pid:
        if not kill_on_owner_death:
            os.close(channel)
            return
        raise SystemExit("original command owner disappeared before guard setup")
    try:
        owner = os.pidfd_open(expected_parent_pid)
    except ProcessLookupError:
        if not kill_on_owner_death:
            os.close(channel)
            return
        raise
    ready_read, ready_write = os.pipe2(os.O_CLOEXEC)
    try:
        child = os.fork()
        if child == 0:
            try:
                if os.fork() != 0:
                    os._exit(0)
                _close_foreign_descriptors((owner, channel, ready_write))
                os.execve(
                    sys.executable,
                    [
                        sys.executable,
                        "-E",
                        "-S",
                        __file__,
                        exec_guard_protocol.GUARD_MODE,
                        str(owner),
                        str(channel),
                        str(ready_write),
                        "1" if kill_on_owner_death else "0",
                    ],
                    {},
                )
            finally:
                os._exit(125)
        os.close(ready_write)
        ready_write = -1
        os.close(channel)
        # The actual workload must not inherit an extra child that changes its
        # waitpid(-1)/wait-all behavior. The native command owner is subreaper
        # for the detached guardian and reaps its exact pidfd on retirement.
        os.waitpid(child, 0)
        if os.read(ready_read, 1) != exec_guard_protocol.GUARD_START:
            if kill_on_owner_death or os.getppid() == expected_parent_pid:
                raise SystemExit("parent-death group guard did not become ready")
        if os.getppid() != expected_parent_pid:
            if kill_on_owner_death:
                os.kill(os.getpid(), signal.SIGTERM)
                raise SystemExit("original command owner disappeared before execution")
    finally:
        os.close(owner)
        os.close(ready_read)
        if ready_write >= 0:
            os.close(ready_write)


def _report_exec_error(descriptor: int, error: OSError) -> int:
    if error.errno is None or error.errno <= 0:
        raise error
    os.write(descriptor, error.errno.to_bytes(exec_guard_protocol.EXEC_ERROR_BYTES, "big"))
    return 125


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        raise SystemExit("exec guard requires a command")
    if (
        args[0] == exec_guard_protocol.GUARD_MODE
        and exec_guard_protocol.PARENT_PID_ENV not in os.environ
    ):
        return _watch_command_owner(args[1:])
    _ensure_owned_session()
    raw_parent = os.environ.pop(exec_guard_protocol.PARENT_PID_ENV, None)
    if raw_parent is None:
        raise SystemExit("exec guard parent identity is missing")
    try:
        expected_parent_pid = int(raw_parent)
    except ValueError:
        raise SystemExit("exec guard parent identity is invalid") from None
    if expected_parent_pid <= 0:
        raise SystemExit("exec guard parent identity is invalid")
    raw_error_fd = os.environ.pop(exec_guard_protocol.EXEC_ERROR_FD_ENV, None)
    if raw_error_fd is None:
        raise SystemExit("exec guard startup error channel is missing")
    try:
        error_fd = int(raw_error_fd)
    except ValueError:
        raise SystemExit("exec guard startup error channel is invalid") from None
    if error_fd < 3:
        raise SystemExit("exec guard startup error channel is invalid")
    # Popen pass_fds makes this descriptor inheritable. A successful kernel
    # exec closes it before the target runs; the guardian closes it separately.
    os.set_inheritable(error_fd, False)
    owner_death = os.environ.pop(exec_guard_protocol.OWNER_DEATH_ENV, "1")
    if owner_death not in {"0", "1"}:
        raise SystemExit("exec guard owner-death policy is invalid")
    kill_on_owner_death = owner_death == "1"
    if kill_on_owner_death:
        _set_parent_death_signal(expected_parent_pid=expected_parent_pid)
    _attach_controlling_terminal()
    raw_exec_fd = os.environ.pop(exec_guard_protocol.EXEC_FD_ENV, None)
    observed_path = os.environ.pop(exec_guard_protocol.OBSERVED_EXECUTABLE_ENV, None)
    if observed_path is not None:
        if raw_exec_fd is not None or not os.path.isabs(observed_path) or "\x00" in observed_path:
            raise SystemExit("exec guard observational execution path is invalid")
        # The original command owner resolved this path. Let the kernel and
        # native /usr/bin/env retain shebang/PATH semantics; this is not atomic
        # execution or a second interpreter resolver.
        _start_parent_guard(expected_parent_pid, kill_on_owner_death=kill_on_owner_death)
        try:
            os.execvpe(observed_path, args, os.environ)
        except OSError as error:
            return _report_exec_error(error_fd, error)
        raise AssertionError("os.execvpe returned")  # pragma: no cover
    if sys.platform.startswith("linux") and raw_exec_fd is None:
        raise SystemExit("exec guard execution descriptor is missing")
    if raw_exec_fd is not None:
        try:
            exec_fd = int(raw_exec_fd)
        except ValueError:
            raise SystemExit("exec guard execution descriptor is invalid") from None
        if exec_fd < 0:
            raise SystemExit("exec guard execution descriptor is invalid")
        _start_parent_guard(expected_parent_pid, kill_on_owner_death=kill_on_owner_death)
        try:
            os.execve(exec_fd, args, os.environ)
        except OSError as error:
            return _report_exec_error(error_fd, error)
        raise AssertionError("os.execve returned")  # pragma: no cover
    try:
        os.execvpe(args[0], args, os.environ)
    except OSError as error:
        return _report_exec_error(error_fd, error)
    raise AssertionError("os.execvpe returned")  # pragma: no cover


if __name__ == "__main__":
    raise SystemExit(main())
