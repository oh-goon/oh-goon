from __future__ import annotations

import errno
import os
from pathlib import Path
from typing import Literal

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.integrations import _copy_transport as subject


def _open_pair(tmp_path: Path, payload: bytes) -> tuple[int, int, Path]:
    source = tmp_path / "source.bin"
    destination = tmp_path / "destination.bin"
    source.write_bytes(payload)
    source_fd = os.open(source, os.O_RDONLY)
    destination_fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    return source_fd, destination_fd, destination


def test_copy_regular_file_uses_safe_leaf_copy_and_preserves_mode(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.write_bytes(b"payload" * 1024)
    source.chmod(0o751)
    destination = tmp_path / "destination"

    copied = subject.copy_regular_file(source, destination)

    assert copied == destination
    assert destination.read_bytes() == source.read_bytes()
    assert destination.stat().st_mode & 0o7777 == 0o751


def test_copy_regular_file_binds_mode_to_opened_source_descriptor_across_path_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    source.write_bytes(b"original-bytes")
    source.chmod(0o640)
    replacement = tmp_path / "replacement"
    replacement.write_bytes(b"replacement-bytes")
    replacement.chmod(0o777)
    destination = tmp_path / "destination"
    checks = 0

    monkeypatch.setattr(subject, "_try_reflink", lambda *_args: False)
    monkeypatch.setattr(subject, "_try_copy_file_range", lambda *_args, **_kwargs: False)

    def replace_path_after_open() -> None:
        nonlocal checks
        checks += 1
        if checks != 2:
            return
        source.unlink()
        replacement.replace(source)

    subject.copy_regular_file(
        source,
        destination,
        check_cancel=replace_path_after_open,
    )

    assert destination.read_bytes() == b"original-bytes"
    assert destination.stat().st_mode & 0o7777 == 0o640
    assert source.read_bytes() == b"replacement-bytes"
    assert source.stat().st_mode & 0o7777 == 0o777


def test_copy_regular_file_rejects_symlink_source_and_existing_destination(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.write_text("payload", encoding="utf-8")
    source_link = tmp_path / "source-link"
    source_link.symlink_to(source)

    with pytest.raises(FleetPlanInvalid, match="without following symlinks"):
        subject.copy_regular_file(source_link, tmp_path / "copy")

    destination = tmp_path / "destination"
    destination.write_text("existing", encoding="utf-8")
    with pytest.raises(FleetPlanInvalid, match="without following symlinks"):
        subject.copy_regular_file(source, destination)
    assert destination.read_text(encoding="utf-8") == "existing"


def test_copy_data_prefers_reflink_when_kernel_accepts_clone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if subject.fcntl is None:
        pytest.skip("fcntl is unavailable")
    payload = b"clone-me" * 1024
    source_fd, destination_fd, destination = _open_pair(tmp_path, payload)

    def clone(destination_descriptor: int, operation: int, source_descriptor: int) -> int:
        assert operation == subject._FICLONE
        data = os.pread(source_descriptor, len(payload), 0)
        assert os.pwrite(destination_descriptor, data, 0) == len(data)
        return 0

    monkeypatch.setattr(subject.fcntl, "ioctl", clone)
    monkeypatch.setattr(
        subject.os,
        "copy_file_range",
        lambda *_args, **_kwargs: pytest.fail("copy_file_range must not run after reflink"),
    )
    try:
        transport = subject.copy_regular_file_data(
            source_fd,
            destination_fd,
            check_cancel=lambda: None,
        )
    finally:
        os.close(source_fd)
        os.close(destination_fd)

    assert transport == "reflink"
    assert destination.read_bytes() == payload


def test_copy_data_uses_kernel_copy_range_after_reflink_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = b"kernel-copy" * 4096
    source_fd, destination_fd, destination = _open_pair(tmp_path, payload)
    monkeypatch.setattr(subject, "_try_reflink", lambda *_args: False)
    calls = 0

    def kernel_copy(source_descriptor: int, destination_descriptor: int, count: int) -> int:
        nonlocal calls
        calls += 1
        data = os.read(source_descriptor, count)
        if not data:
            return 0
        return os.write(destination_descriptor, data)

    monkeypatch.setattr(subject.os, "copy_file_range", kernel_copy)
    try:
        transport = subject.copy_regular_file_data(
            source_fd,
            destination_fd,
            check_cancel=lambda: None,
        )
    finally:
        os.close(source_fd)
        os.close(destination_fd)

    assert transport == "copy_file_range"
    assert calls >= 2
    assert destination.read_bytes() == payload


def test_copy_data_resumes_stream_from_partial_kernel_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = b"0123456789" * 4096
    source_fd, destination_fd, destination = _open_pair(tmp_path, payload)
    monkeypatch.setattr(subject, "_try_reflink", lambda *_args: False)
    calls = 0

    def partial_then_refuse(
        source_descriptor: int, destination_descriptor: int, _count: int
    ) -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            data = os.read(source_descriptor, 137)
            return os.write(destination_descriptor, data)
        raise OSError(errno.EXDEV, "cross-device kernel copy unavailable")

    monkeypatch.setattr(subject.os, "copy_file_range", partial_then_refuse)
    try:
        transport = subject.copy_regular_file_data(
            source_fd,
            destination_fd,
            check_cancel=lambda: None,
        )
    finally:
        os.close(source_fd)
        os.close(destination_fd)

    assert transport == "stream"
    assert calls == 2
    assert destination.read_bytes() == payload


def test_copy_data_keeps_cancellation_checks_on_stream_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_fd, destination_fd, _destination = _open_pair(tmp_path, b"x" * (2 * 1024 * 1024))
    monkeypatch.setattr(subject, "_try_reflink", lambda *_args: False)
    monkeypatch.setattr(subject, "_try_copy_file_range", lambda *_args, **_kwargs: False)
    checks = 0

    def cancel() -> None:
        nonlocal checks
        checks += 1
        if checks > 3:
            raise RuntimeError("cancelled")

    try:
        with pytest.raises(RuntimeError, match="cancelled"):
            subject.copy_regular_file_data(source_fd, destination_fd, check_cancel=cancel)
    finally:
        os.close(source_fd)
        os.close(destination_fd)

    assert checks > 3


def test_copy_limit_admits_exact_size_and_refuses_excess_before_destination_creation(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.write_bytes(b"bounded")
    destination = tmp_path / "exact"
    subject.copy_regular_file(source, destination, max_bytes=7)
    assert destination.read_bytes() == b"bounded"
    refused = tmp_path / "refused"
    with pytest.raises(subject.FileCopyLimitExceeded):
        subject.copy_regular_file(source, refused, max_bytes=6)
    assert not refused.exists()


@pytest.mark.parametrize("transport", ["reflink", "copy_file_range", "stream"])
@pytest.mark.parametrize("grow", [False, True])
def test_bounded_copy_preserves_native_transport_and_refuses_source_growth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    transport: Literal["reflink", "copy_file_range", "stream"],
    grow: bool,
) -> None:
    payload = b"bounded"
    source_fd, destination_fd, destination = _open_pair(tmp_path, payload)
    source = tmp_path / "source.bin"
    read = os.read
    write = os.write
    grew = False
    requested: list[int] = []

    def grow_source() -> None:
        nonlocal grew
        if grow and not grew:
            with source.open("ab") as stream:
                stream.write(b"unexpected-growth")
            grew = True

    def clone(source_descriptor: int, destination_descriptor: int) -> bool:
        if transport != "reflink":
            return False
        grow_source()
        data = os.pread(source_descriptor, source.stat().st_size, 0)
        assert os.pwrite(destination_descriptor, data, 0) == len(data)
        return True

    def kernel_copy(source_descriptor: int, destination_descriptor: int, count: int) -> int:
        if transport == "stream":
            raise OSError(errno.EXDEV, "kernel copy unavailable")
        requested.append(count)
        grow_source()
        data = read(source_descriptor, count)
        return write(destination_descriptor, data) if data else 0

    def bounded_read(descriptor: int, count: int) -> bytes:
        if descriptor == source_fd:
            requested.append(count)
            grow_source()
        return read(descriptor, count)

    monkeypatch.setattr(subject, "_try_reflink", clone)
    monkeypatch.setattr(os, "copy_file_range", kernel_copy, raising=False)
    monkeypatch.setattr(os, "read", bounded_read)
    try:
        if grow:
            with pytest.raises(subject.FileCopyLimitExceeded):
                subject.copy_regular_file_data(
                    source_fd, destination_fd, check_cancel=lambda: None, max_bytes=len(payload)
                )
        else:
            observed = subject.copy_regular_file_data(
                source_fd, destination_fd, check_cancel=lambda: None, max_bytes=len(payload)
            )
            assert observed == transport
            assert destination.read_bytes() == payload
    finally:
        os.close(source_fd)
        os.close(destination_fd)
    assert destination.stat().st_size <= len(payload)
    assert all(0 < count <= len(payload) for count in requested)


def test_bounded_copy_continues_partial_kernel_prefix_through_stream_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = b"bounded-copy"
    source_fd, destination_fd, destination = _open_pair(tmp_path, payload)
    monkeypatch.setattr(subject, "_try_reflink", lambda *_args: False)
    calls = 0

    def partial_then_refuse(source_descriptor: int, destination_descriptor: int, count: int) -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            return os.write(destination_descriptor, os.read(source_descriptor, min(count, 3)))
        raise OSError(errno.EXDEV, "kernel copy unavailable after prefix")

    monkeypatch.setattr(os, "copy_file_range", partial_then_refuse, raising=False)
    try:
        observed = subject.copy_regular_file_data(
            source_fd, destination_fd, check_cancel=lambda: None, max_bytes=len(payload)
        )
    finally:
        os.close(source_fd)
        os.close(destination_fd)
    assert observed == "stream"
    assert destination.read_bytes() == payload


def test_bounded_copy_admits_empty_file(tmp_path: Path) -> None:
    source = tmp_path / "empty"
    source.touch()
    destination = tmp_path / "copy"
    subject.copy_regular_file(source, destination, max_bytes=0)
    assert destination.read_bytes() == b""


@pytest.mark.parametrize("invalid", [-1, True])
def test_invalid_copy_limit_refuses_before_creating_destination(
    tmp_path: Path, invalid: int
) -> None:
    source = tmp_path / "source"
    source.write_bytes(b"bounded")
    destination = tmp_path / "refused"
    with pytest.raises(ValueError, match="max_bytes"):
        subject.copy_regular_file(source, destination, max_bytes=invalid)
    assert not destination.exists()


def test_bounded_copy_cancellation_closes_owned_file_descriptors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    source.write_bytes(b"bounded")
    destination = tmp_path / "copy"
    opened: list[int] = []
    original_open = os.open

    def track_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        descriptor = original_open(path, flags, mode, dir_fd=dir_fd)
        if os.fspath(path) in {str(source), str(destination)}:
            opened.append(descriptor)
        return descriptor

    checks = 0

    def cancel() -> None:
        nonlocal checks
        checks += 1
        if checks == 2:
            raise RuntimeError("cancelled")

    monkeypatch.setattr(os, "open", track_open)
    with pytest.raises(RuntimeError, match="cancelled"):
        subject.copy_regular_file(source, destination, max_bytes=7, check_cancel=cancel)
    assert len(opened) == 2
    for descriptor in opened:
        with pytest.raises(OSError) as closed:
            os.fstat(descriptor)
        assert closed.value.errno == errno.EBADF
