from __future__ import annotations

import os
import subprocess
import tarfile
import threading
from pathlib import Path


def fleet_file(root: Path, relative: str) -> Path:
    value = Path(relative)
    if value.is_absolute() or ".." in value.parts:
        raise ValueError(f"Fleet runner file reference must be normalized relative: {relative!r}")
    fleet_root = root.parent.resolve()
    candidate = (fleet_root / value).resolve(strict=True)
    if not candidate.is_relative_to(fleet_root) or not candidate.is_file():
        raise ValueError(f"Fleet runner file reference escapes or is missing: {relative!r}")
    return candidate


def extract_tree(archive_path: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    with tarfile.open(archive_path, mode="r:gz") as archive:
        for member in archive.getmembers():
            target = (root / member.name).resolve()
            if not target.is_relative_to(root):
                raise ValueError("Fleet runner archive escapes destination")
        archive.extractall(root, filter="data")


def archive_tree(root: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(destination, mode="w:gz", format=tarfile.PAX_FORMAT) as archive:
        for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
            archive.add(path, arcname=path.relative_to(root).as_posix(), recursive=False)


class ByteCapture:
    def __init__(self, limit_bytes: int | None, overflow: threading.Event, stream: str) -> None:
        self.limit_bytes = limit_bytes
        self._overflow = overflow
        self._stream = stream
        self._payload = bytearray()
        self.overflow_stream: str | None = None

    @property
    def payload(self) -> bytes:
        return bytes(self._payload)

    def append(self, chunk: bytes) -> None:
        if self.limit_bytes is None:
            self._payload.extend(chunk)
            return
        if self.overflow_stream is not None:
            return
        remaining = self.limit_bytes - len(self._payload)
        if remaining > 0:
            self._payload.extend(chunk[:remaining])
        if len(chunk) > remaining:
            self.overflow_stream = self._stream
            self._overflow.set()


def read_pipe(descriptor: int, capture: ByteCapture) -> None:
    try:
        while True:
            chunk = os.read(descriptor, 64 * 1024)
            if not chunk:
                break
            capture.append(chunk)
    finally:
        os.close(descriptor)


def complete_child_result(
    exec_argv: list[str],
    timeout: float | None,
    status: int | None,
    *,
    timed_out: bool,
    output_limited: bool,
    stdout_capture: ByteCapture,
    stderr_capture: ByteCapture,
) -> subprocess.CompletedProcess[str]:
    stdout = stdout_capture.payload.decode("utf-8", errors="replace")
    stderr = stderr_capture.payload.decode("utf-8", errors="replace")
    if output_limited:
        stream = stdout_capture.overflow_stream or stderr_capture.overflow_stream or "stdout"
        limit = stdout_capture.limit_bytes if stream == "stdout" else stderr_capture.limit_bytes
        raise RuntimeError(
            f"Fleet OpenSandbox {stream} exceeded the {limit}-byte capture limit; "
            "output is bounded to protect executor memory"
        )
    if timed_out:
        raise subprocess.TimeoutExpired(exec_argv, timeout, output=stdout, stderr=stderr)
    if status is None:
        raise RuntimeError("Fleet runner lost child process status")
    return subprocess.CompletedProcess(exec_argv, os.waitstatus_to_exitcode(status), stdout, stderr)
