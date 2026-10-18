from __future__ import annotations

import hashlib
import json
import os
import shlex
import stat
from dataclasses import dataclass
from pathlib import Path

from oh_goon.core.errors import ExecutionAuthorityDivergence

try:  # pragma: no cover - import availability is platform-owned
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]

_COPY_CHUNK_BYTES = 1024 * 1024
_LINUX_F_ADD_SEALS = 1033
_LINUX_F_GET_SEALS = 1034
_LINUX_F_SEAL_SEAL = 0x0001
_LINUX_F_SEAL_SHRINK = 0x0002
_LINUX_F_SEAL_GROW = 0x0004
_LINUX_F_SEAL_WRITE = 0x0008


@dataclass(frozen=True, slots=True)
class SealedExecutionBytes:
    """One immutable Linux descriptor containing exact execution bytes."""

    descriptor: int
    identity: str
    byte_count: int
    mode: int
    kernel_device: int
    kernel_inode: int

    @property
    def proc_path(self) -> str:
        return f"/proc/self/fd/{self.descriptor}"


def atomic_execution_bytes_supported() -> bool:
    return (
        fcntl is not None
        and hasattr(os, "memfd_create")
        and bool(getattr(os, "MFD_ALLOW_SEALING", 0))
        and os.path.isdir("/proc/self/fd")
    )


def seal_execution_bytes(
    source: Path,
    *,
    label: str,
    expected_identity: str | None = None,
) -> SealedExecutionBytes:
    """Open once, copy once, seal, and return the exact bytes admitted for execution."""

    if not atomic_execution_bytes_supported():
        raise _divergence("atomic executable binding requires Linux memfd sealing")
    source_fd = _open_regular_source(source, label=label)
    memfd = -1
    try:
        metadata = os.fstat(source_fd)
        flags = getattr(os, "MFD_CLOEXEC", 0) | getattr(os, "MFD_ALLOW_SEALING", 0)
        memfd = os.memfd_create(f"oh-goon-{label.replace(' ', '-')}", flags=flags)
        digest = hashlib.sha256()
        while True:
            chunk = os.read(source_fd, _COPY_CHUNK_BYTES)
            if not chunk:
                break
            digest.update(chunk)
            _write_all(memfd, chunk)
        observed = "sha256:" + digest.hexdigest()
        if expected_identity is not None and observed != expected_identity:
            raise _divergence(
                f"{label} changed before atomic launch binding: expected "
                f"{expected_identity}, observed {observed}"
            )
        os.fchmod(memfd, stat.S_IMODE(metadata.st_mode))
        add_seals, get_seals, required_seals = _linux_seal_operations()
        try:
            assert fcntl is not None
            fcntl.fcntl(memfd, add_seals, required_seals)
            applied = fcntl.fcntl(memfd, get_seals)
        except OSError as exc:
            raise _divergence(f"{label} memfd cannot apply Linux write seals") from exc
        if applied & required_seals != required_seals:
            raise _divergence(f"{label} memfd did not retain all required write seals")
        os.lseek(memfd, 0, os.SEEK_SET)
        kernel = os.fstat(memfd)
        return SealedExecutionBytes(
            descriptor=memfd,
            identity=observed,
            byte_count=metadata.st_size,
            mode=stat.S_IMODE(metadata.st_mode),
            kernel_device=kernel.st_dev,
            kernel_inode=kernel.st_ino,
        )
    except BaseException:
        if memfd >= 0:
            os.close(memfd)
        raise
    finally:
        os.close(source_fd)


def verify_sealed_execution_descriptor(sealed: SealedExecutionBytes) -> None:
    """Revalidate the SAME sealed kernel object, not an interchangeable FD number.

    A caller can replace an integer descriptor after source capture. Checking
    F_GET_SEALS alone would accept a different, sealed memfd. The device/inode
    pair is captured when the original immutable bytes are sealed. This is
    FD custody at the local launch boundary, not proof of a native mount.
    """
    if not atomic_execution_bytes_supported():
        raise _divergence("atomic runtime FD custody requires Linux memfd sealing")
    try:
        info = os.fstat(sealed.descriptor)
        if (not stat.S_ISREG(info.st_mode)
                or (info.st_dev, info.st_ino) != (sealed.kernel_device, sealed.kernel_inode)
                or info.st_size != sealed.byte_count
                or stat.S_IMODE(info.st_mode) != sealed.mode):
            raise _divergence("atomic runtime kernel FD identity changed before launch")
        _, get_seals, required_seals = _linux_seal_operations()
        assert fcntl is not None
        if fcntl.fcntl(sealed.descriptor, get_seals) & required_seals != required_seals:
            raise _divergence("atomic runtime kernel FD lost mandatory write seals")
        hasher = hashlib.sha256()
        offset = 0
        while offset < sealed.byte_count:
            chunk = os.pread(sealed.descriptor, min(_COPY_CHUNK_BYTES, sealed.byte_count - offset), offset)
            if not chunk:
                raise _divergence("atomic runtime kernel FD has truncated bytes")
            hasher.update(chunk)
            offset += len(chunk)
        if "sha256:" + hasher.hexdigest() != sealed.identity:
            raise _divergence("atomic runtime kernel FD bytes differ from original authority")
        after = os.fstat(sealed.descriptor)
        if (after.st_dev, after.st_ino, after.st_size, stat.S_IMODE(after.st_mode)) != (
                info.st_dev, info.st_ino, info.st_size, stat.S_IMODE(info.st_mode)):
            raise _divergence("atomic runtime kernel FD changed during custody verification")
    except OSError as exc:
        raise _divergence("atomic runtime kernel FD cannot be verified before launch") from exc


def sealed_execution_shebang(
    sealed: SealedExecutionBytes,
    *,
    label: str,
) -> tuple[str, ...] | None:
    """Read interpreter selection from the sealed bytes, never from the live source path."""

    first = os.pread(sealed.descriptor, 4096, 0).split(b"\n", 1)[0]
    if not first.startswith(b"#!"):
        return None
    try:
        text = first[2:].decode("utf-8", errors="strict").strip()
        parts = tuple(shlex.split(text))
    except (UnicodeDecodeError, ValueError) as exc:
        raise _divergence(f"{label} has invalid shebang") from exc
    if not parts:
        raise _divergence(f"{label} has empty shebang")
    return parts


def execution_materialization_identity(
    executable: SealedExecutionBytes,
    interpreter: SealedExecutionBytes | None,
) -> str:
    payload = {
        "schema": "oh-goon.atomic-execution-materialization/v1",
        "mechanism": "linux-sealed-memfd",
        "executable": _sealed_identity_payload(executable),
        "interpreter": (
            _sealed_identity_payload(interpreter) if interpreter is not None else None
        ),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _sealed_identity_payload(value: SealedExecutionBytes) -> dict[str, object]:
    return {
        "identity": value.identity,
        "byte_count": value.byte_count,
        "mode": value.mode,
    }


def _linux_seal_operations() -> tuple[int, int, int]:
    if fcntl is None:
        raise _divergence("atomic executable binding requires Linux file sealing")
    add = getattr(fcntl, "F_ADD_SEALS", _LINUX_F_ADD_SEALS)
    get = getattr(fcntl, "F_GET_SEALS", _LINUX_F_GET_SEALS)
    required = (
        getattr(fcntl, "F_SEAL_WRITE", _LINUX_F_SEAL_WRITE)
        | getattr(fcntl, "F_SEAL_GROW", _LINUX_F_SEAL_GROW)
        | getattr(fcntl, "F_SEAL_SHRINK", _LINUX_F_SEAL_SHRINK)
        | getattr(fcntl, "F_SEAL_SEAL", _LINUX_F_SEAL_SEAL)
    )
    return add, get, required


def _open_regular_source(path: Path, *, label: str) -> int:
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise _divergence(f"cannot resolve {label} source: {path}") from exc
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(resolved, flags)
    except OSError as exc:
        raise _divergence(f"cannot open {label} source without following a final symlink") from exc
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise _divergence(f"{label} source is not a regular file")
    return descriptor


def _write_all(descriptor: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError("short write while sealing runtime executable")
        view = view[written:]


def _divergence(message: str) -> ExecutionAuthorityDivergence:
    return ExecutionAuthorityDivergence(message)
