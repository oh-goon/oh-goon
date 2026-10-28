"""Atomic persistence and schema authority for bounded pytest execution reports."""

from __future__ import annotations

import json
import os
import secrets
import stat
from pathlib import Path

PYTEST_BATCH_EXECUTION_REPORT_SCHEMA_FAMILY = "oh-goon.pytest-batch-execution"
PYTEST_BATCH_EXECUTION_REPORT_SCHEMA_VERSION = 1


def execution_report_schema() -> str:
    family = PYTEST_BATCH_EXECUTION_REPORT_SCHEMA_FAMILY
    version = PYTEST_BATCH_EXECUTION_REPORT_SCHEMA_VERSION
    if not isinstance(family, str) or family.strip() != family or not family:
        raise RuntimeError("pytest batch execution-report schema family owner is invalid")
    if not isinstance(version, int) or isinstance(version, bool) or version <= 0:
        raise RuntimeError("pytest batch execution-report schema version owner is invalid")
    return f"{family}/v{version}"


def write_execution_report(path: Path, payload: dict[str, object]) -> None:
    expected_schema = execution_report_schema()
    if payload.get("schema") != expected_schema:
        raise RuntimeError("pytest batch execution report schema does not match owner")
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
    path = path.absolute()
    directory = _open_report_directory(path.parent)
    temporary = f".{path.name}.{secrets.token_hex(16)}.tmp"
    try:
        _admit_report_destination(directory, path.name)
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=directory,
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            created = True
            authority = os.fstat(stream.fileno())
            try:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
                if not _report_file_matches(directory, temporary, authority):
                    raise RuntimeError("pytest execution report temporary file changed")
                _admit_report_destination(directory, path.name)
                _admit_report_directory_location(path.parent, directory)
                os.replace(temporary, path.name, src_dir_fd=directory, dst_dir_fd=directory)
                created = False
                if not _report_file_matches(directory, path.name, authority):
                    raise RuntimeError("pytest execution report publication changed")
                _admit_report_directory_location(path.parent, directory)
                os.fsync(directory)
            finally:
                if created and _report_file_matches(directory, temporary, authority):
                    os.unlink(temporary, dir_fd=directory)
    finally:
        os.close(directory)


def _open_report_directory(path: Path, *, create: bool = True) -> int:
    """Pin native directory authority without following any path-component symlink."""

    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise RuntimeError("safe pytest execution reports require native no-follow directories")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    directory = os.open(path.anchor, flags)
    try:
        for part in path.parts[1:]:
            try:
                child = os.open(part, flags, dir_fd=directory)
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    os.mkdir(part, 0o700, dir_fd=directory)
                except FileExistsError:
                    pass
                child = os.open(part, flags, dir_fd=directory)
            os.close(directory)
            directory = child
        observed = os.fstat(directory)
        if observed.st_uid != os.geteuid() or observed.st_mode & 0o022:
            raise RuntimeError(
                "pytest execution report directory must be owned and not writable by other users"
            )
        return directory
    except BaseException:
        os.close(directory)
        raise


def _admit_report_destination(directory: int, name: str) -> None:
    try:
        observed = os.stat(name, dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError:
        return
    if not stat.S_ISREG(observed.st_mode) or observed.st_uid != os.geteuid():
        raise RuntimeError("pytest execution report destination must be an owned regular file")


def _report_file_matches(directory: int, name: str, authority: os.stat_result) -> bool:
    try:
        observed = os.stat(name, dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError:
        return False
    return (observed.st_dev, observed.st_ino) == (authority.st_dev, authority.st_ino)


def _admit_report_directory_location(path: Path, directory: int) -> None:
    authority = os.fstat(directory)
    try:
        current = _open_report_directory(path, create=False)
    except (OSError, RuntimeError) as exc:
        raise RuntimeError("pytest execution report directory changed during publication") from exc
    try:
        observed = os.fstat(current)
        if (observed.st_dev, observed.st_ino) != (authority.st_dev, authority.st_ino):
            raise RuntimeError("pytest execution report directory changed during publication")
    finally:
        os.close(current)
