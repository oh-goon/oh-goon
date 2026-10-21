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

import ctypes
import os
import signal
import sys

if __package__:
    from oh_goon.runtime import exec_guard_protocol
else:  # Direct `python -S path/to/exec_guard.py` launch used by the command owner.
    import exec_guard_protocol


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


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        raise SystemExit("exec guard requires a command")
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
        os.execvpe(observed_path, args, os.environ)
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
        os.execve(exec_fd, args, os.environ)
        raise AssertionError("os.execve returned")  # pragma: no cover
    os.execvpe(args[0], args, os.environ)
    raise AssertionError("os.execvpe returned")  # pragma: no cover


if __name__ == "__main__":
    raise SystemExit(main())
