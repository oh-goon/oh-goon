from __future__ import annotations

import os
import stat
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from oh_goon.core.errors import FleetPlanInvalid

try:
    import fcntl
except ImportError:  # pragma: no cover - strict execution is Linux, import keeps tooling portable.
    fcntl = None  # type: ignore[assignment]

CopyTransport = Literal["reflink", "copy_file_range", "stream"]

# Linux UAPI: _IOW(0x94, 9, int). Python does not expose FICLONE consistently.
_FICLONE = 0x40049409
_KERNEL_COPY_CHUNK_BYTES = 16 * 1024 * 1024
_STREAM_COPY_CHUNK_BYTES = 1024 * 1024


class FileCopyLimitExceeded(FleetPlanInvalid):
    """The selected source or copied file exceeds its explicit byte bound."""


def copy_regular_file(
    source: str | Path,
    destination: str | Path,
    *,
    check_cancel: Callable[[], None] | None = None,
    label: str = "owned file copy",
    max_bytes: int | None = None,
) -> Path:
    """Copy one regular-file leaf through safe descriptors and preserve its mode.

    Reflink and kernel-copy acceleration are transport details only. Source and
    destination path authority stays no-follow/exclusive, and callers that own a
    stronger content identity continue to verify that identity independently.
    """

    _validate_copy_limit(max_bytes)
    source_path = Path(source)
    destination_path = Path(destination)
    cancel = check_cancel or _noop
    cancel()
    source_fd = _open_regular_nofollow(source_path, os.O_RDONLY, label=label)
    try:
        source_mode = stat.S_IMODE(os.fstat(source_fd).st_mode)
        _require_copy_limit(source_fd, max_bytes)
        destination_fd = _open_regular_nofollow(
            destination_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            mode=0o600,
            label=label,
        )
        try:
            copy_regular_file_data(
                source_fd, destination_fd, check_cancel=cancel, max_bytes=max_bytes
            )
            os.fchmod(destination_fd, source_mode)
            _drop_clean_file_cache(source_fd)
        finally:
            os.close(destination_fd)
    finally:
        os.close(source_fd)
    return destination_path


def copy_regular_file_data(
    source_fd: int,
    destination_fd: int,
    *,
    check_cancel: Callable[[], None],
    max_bytes: int | None = None,
) -> CopyTransport:
    """Copy bytes between caller-validated regular-file descriptors."""

    _validate_copy_limit(max_bytes)
    check_cancel()
    _require_copy_limit(source_fd, max_bytes)
    _require_copy_limit(destination_fd, max_bytes)
    if _try_reflink(source_fd, destination_fd):
        if max_bytes is not None and os.fstat(destination_fd).st_size > max_bytes:
            os.ftruncate(destination_fd, max_bytes)
            raise FileCopyLimitExceeded(f"File copy exceeded the {max_bytes}-byte limit")
        _require_copy_limit(source_fd, max_bytes)
        check_cancel()
        return "reflink"

    if _try_copy_file_range(
        source_fd, destination_fd, check_cancel=check_cancel, max_bytes=max_bytes
    ):
        check_cancel()
        _require_copy_limit(source_fd, max_bytes)
        return "copy_file_range"

    _stream_copy(source_fd, destination_fd, check_cancel=check_cancel, max_bytes=max_bytes)
    check_cancel()
    _require_copy_limit(source_fd, max_bytes)
    return "stream"


def _try_reflink(source_fd: int, destination_fd: int) -> bool:
    if fcntl is None:
        return False
    try:
        fcntl.ioctl(destination_fd, _FICLONE, source_fd)
    except OSError:
        # Filesystem/mount support is optional. Reflink never becomes a correctness
        # requirement or an execution-authority fact.
        return False
    return True


def _try_copy_file_range(
    source_fd: int,
    destination_fd: int,
    *,
    check_cancel: Callable[[], None],
    max_bytes: int | None = None,
) -> bool:
    copy_file_range = getattr(os, "copy_file_range", None)
    if copy_file_range is None:
        return False

    copied = 0
    while True:
        check_cancel()
        _require_copy_limit(source_fd, max_bytes)
        count_limit = _KERNEL_COPY_CHUNK_BYTES
        if max_bytes is not None:
            remaining = max_bytes - os.lseek(destination_fd, 0, os.SEEK_CUR)
            if remaining <= 0:
                return True
            count_limit = min(count_limit, remaining)
        try:
            count = copy_file_range(
                source_fd,
                destination_fd,
                count_limit,
            )
        except OSError:
            # Continue the canonical stream path from the exact successful prefix.
            os.lseek(source_fd, copied, os.SEEK_SET)
            os.lseek(destination_fd, copied, os.SEEK_SET)
            return False
        if count == 0:
            return True
        copied += count


def _stream_copy(
    source_fd: int,
    destination_fd: int,
    *,
    check_cancel: Callable[[], None],
    max_bytes: int | None = None,
) -> None:
    while True:
        check_cancel()
        _require_copy_limit(source_fd, max_bytes)
        count_limit = _STREAM_COPY_CHUNK_BYTES
        if max_bytes is not None:
            remaining = max_bytes - os.lseek(destination_fd, 0, os.SEEK_CUR)
            if remaining <= 0:
                return
            count_limit = min(count_limit, remaining)
        chunk = os.read(source_fd, count_limit)
        if not chunk:
            return
        view = memoryview(chunk)
        while view:
            check_cancel()
            written = os.write(destination_fd, view)
            if written <= 0:  # defensive: regular-file writes must make progress.
                raise OSError("snapshot copy made no write progress")
            view = view[written:]


def _validate_copy_limit(max_bytes: int | None) -> None:
    if max_bytes is not None and (type(max_bytes) is not int or max_bytes < 0):
        raise ValueError("File copy max_bytes must be a nonnegative integer")


def _require_copy_limit(descriptor: int, max_bytes: int | None) -> None:
    if max_bytes is not None and os.fstat(descriptor).st_size > max_bytes:
        raise FileCopyLimitExceeded(f"File copy exceeded the {max_bytes}-byte limit")


def _open_regular_nofollow(path: Path, flags: int, *, label: str, mode: int = 0o600) -> int:
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    cloexec = getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(path, flags | nofollow | cloexec, mode)
    except OSError as exc:
        raise FleetPlanInvalid(
            f"Cannot open {label} without following symlinks: {path}: {exc}"
        ) from exc
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise FleetPlanInvalid(f"{label} is not a regular file: {path}")
    return descriptor


def _drop_clean_file_cache(descriptor: int) -> None:
    advise = getattr(os, "posix_fadvise", None)
    dontneed = getattr(os, "POSIX_FADV_DONTNEED", None)
    if advise is None or dontneed is None:
        return
    try:
        advise(descriptor, 0, 0, dontneed)
    except OSError:
        return


def _noop() -> None:
    return None
