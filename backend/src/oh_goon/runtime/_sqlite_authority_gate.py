from __future__ import annotations

import errno
import hashlib
import os
import stat
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from threading import RLock, local

from oh_goon.core.path_authority import ensure_private_directory

_GATE_DIRECTORY = ".sqlite-authority"
_GATE_ACQUIRE_TIMEOUT_SECONDS = 30.0
_GATE_RETRY_SECONDS = 0.01
_thread_state = local()
_descriptor_guard = RLock()
_open_gate_descriptors: set[int] = set()


def _after_fork_child() -> None:
    # before-fork owns _descriptor_guard in the forking thread, so no vanished sibling can
    # leave this callback waiting on a lock. Drop every inherited gate descriptor, including
    # descriptors opened by parent threads that do not survive fork().
    for descriptor in tuple(_open_gate_descriptors):
        try:
            os.close(descriptor)
        except OSError:
            pass
    _open_gate_descriptors.clear()
    _thread_state.holds = {}
    _thread_state.owner_pid = os.getpid()
    _descriptor_guard.release()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(
        before=_descriptor_guard.acquire,
        after_in_parent=_descriptor_guard.release,
        after_in_child=_after_fork_child,
    )


def _gate_path(database_path: Path) -> Path:
    database = database_path.expanduser().resolve()
    root = ensure_private_directory(database.parent / _GATE_DIRECTORY)
    identity = hashlib.sha256(str(database).encode("utf-8")).hexdigest()[:24]
    return root / f"{identity}.lock"


def _thread_holds() -> dict[str, tuple[str, int, int]]:
    current_pid = os.getpid()
    owner_pid = getattr(_thread_state, "owner_pid", None)
    holds = getattr(_thread_state, "holds", None)
    if owner_pid != current_pid or holds is None:
        holds = {}
        _thread_state.holds = holds
        _thread_state.owner_pid = current_pid
    return holds


def _open_gate(path: Path) -> int:
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    with _descriptor_guard:
        descriptor = os.open(path, flags, 0o600)
        _open_gate_descriptors.add(descriptor)
    try:
        os.fchmod(descriptor, 0o600)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise RuntimeError(f"SQLite authority gate is not a regular file: {path}")
        if os.name == "nt" and metadata.st_size == 0:
            os.write(descriptor, b"0")
            os.fsync(descriptor)
        os.lseek(descriptor, 0, os.SEEK_SET)
        return descriptor
    except BaseException:
        _close_gate(descriptor)
        raise


def _close_gate(descriptor: int) -> None:
    with _descriptor_guard:
        _open_gate_descriptors.discard(descriptor)
        try:
            os.close(descriptor)
        except OSError:
            pass


def _try_kernel_gate(descriptor: int, *, write: bool) -> bool:
    if os.name == "posix":
        import fcntl

        operation = (fcntl.LOCK_EX if write else fcntl.LOCK_SH) | fcntl.LOCK_NB
        try:
            fcntl.flock(descriptor, operation)
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EAGAIN}:
                return False
            raise
        return True
    if sys.platform == "win32":
        import msvcrt

        # Native Windows exposes only exclusive byte-range locking through stdlib. Serialize
        # reads too rather than silently weakening cross-process authority.
        os.lseek(descriptor, 0, os.SEEK_SET)
        try:
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                return False
            raise
        return True
    raise RuntimeError("SQLite cross-process authority gate is unsupported on this platform")


def _acquire_kernel_gate(descriptor: int, *, write: bool) -> None:
    deadline = time.monotonic() + _GATE_ACQUIRE_TIMEOUT_SECONDS
    while not _try_kernel_gate(descriptor, write=write):
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"SQLite authority gate remained busy for {_GATE_ACQUIRE_TIMEOUT_SECONDS:.0f}s"
            )
        time.sleep(_GATE_RETRY_SECONDS)


def _release_kernel_gate(descriptor: int) -> None:
    if os.name == "posix":
        import fcntl

        fcntl.flock(descriptor, fcntl.LOCK_UN)
        return
    if sys.platform == "win32":
        import msvcrt

        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        return
    raise RuntimeError("SQLite cross-process authority gate is unsupported on this platform")


@contextmanager
def sqlite_authority_gate(database_path: Path, *, write: bool) -> Iterator[None]:
    """Coordinate canonical SQLite cuts across Oh-Goon threads and processes.

    SQLite remains transaction and durability authority. This advisory gate only prevents an
    Oh-Goon writer from changing rollback-journal pages while another Oh-Goon owner opens,
    configures, or observes the same database. POSIX readers share the gate; native Windows
    serializes cuts because its stdlib lock primitive is exclusive-only. Acquisition is bounded,
    lock bytes are not durable product state, and kernel ownership dies with the process.
    """

    path = _gate_path(database_path)
    key = str(path)
    holds = _thread_holds()
    held = holds.get(key)
    requested = "write" if write else "read"
    if held is not None:
        mode, depth, descriptor = held
        if write and mode != "write":
            raise RuntimeError(
                "SQLite authority gate cannot upgrade a live read cut to write authority"
            )
        holds[key] = (mode, depth + 1, descriptor)
        try:
            yield
        finally:
            current_mode, current_depth, current_descriptor = holds[key]
            if current_depth == 1:
                holds.pop(key, None)
            else:
                holds[key] = (current_mode, current_depth - 1, current_descriptor)
        return

    descriptor = _open_gate(path)
    try:
        _acquire_kernel_gate(descriptor, write=write)
        holds[key] = (requested, 1, descriptor)
        try:
            yield
        finally:
            holds.pop(key, None)
            _release_kernel_gate(descriptor)
    finally:
        _close_gate(descriptor)
