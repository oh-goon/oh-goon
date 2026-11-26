"""Read a bounded operator-selected task document for a writable Schedule."""

from __future__ import annotations

import json
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from oh_goon.core.errors import InvalidRequest
from oh_goon.core.models.writable_schedule import (
    ApprovedTaskBatch,
    ApprovedTaskDocument,
    WritableScheduleTask,
)

_MAX_DOCUMENT_BYTES = 2 * 1024 * 1024


class ApprovedTaskDocumentError(InvalidRequest):
    """The configured task publication cannot provide valid approval today."""


@contextmanager
def _open_directory(path: Path) -> Iterator[int]:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        yield descriptor
    finally:
        os.close(descriptor)


def bind_task_document(path: Path, *, protected_roots: tuple[Path, ...]) -> ApprovedTaskDocument:
    if not path.is_absolute() or ".." in path.parts or not path.name:
        raise ApprovedTaskDocumentError("Choose an absolute approved task document path")
    try:
        directory = path.parent.resolve(strict=True)
        resolved = directory / path.name
        if any(resolved.is_relative_to(root.resolve(strict=True)) for root in protected_roots):
            raise ApprovedTaskDocumentError(
                "The approved task document must be outside the writable checkout and branch owner"
            )
        with _open_directory(directory) as descriptor:
            metadata = os.fstat(descriptor)
            try:
                entry = os.stat(resolved.name, dir_fd=descriptor, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                if not stat.S_ISREG(entry.st_mode):
                    raise ApprovedTaskDocumentError(
                        "The approved task document must be a regular file"
                    )
    except (OSError, RuntimeError):
        raise ApprovedTaskDocumentError(
            "Cannot bind the approved task document directory"
        ) from None
    return ApprovedTaskDocument(
        path=resolved, directory_device=metadata.st_dev, directory_inode=metadata.st_ino
    )


def read_approved_tasks(document: ApprovedTaskDocument) -> tuple[WritableScheduleTask, ...]:
    try:
        with _open_directory(document.path.parent) as directory:
            metadata = os.fstat(directory)
            if (metadata.st_dev, metadata.st_ino) != (
                document.directory_device,
                document.directory_inode,
            ):
                raise ApprovedTaskDocumentError("The approved task document directory changed")
            try:
                leaf = os.open(
                    document.path.name,
                    os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                    dir_fd=directory,
                )
            except FileNotFoundError:
                return ()
            try:
                before = os.fstat(leaf)
                if not stat.S_ISREG(before.st_mode) or before.st_size > _MAX_DOCUMENT_BYTES:
                    raise ApprovedTaskDocumentError(
                        "The approved task document must be a regular file of at most 2 MiB"
                    )
                with open(leaf, "rb", closefd=False) as stream:
                    payload = stream.read(_MAX_DOCUMENT_BYTES + 1)
                after = os.fstat(leaf)
            finally:
                os.close(leaf)
    except OSError:
        raise ApprovedTaskDocumentError("Cannot read the approved task document") from None
    if len(payload) > _MAX_DOCUMENT_BYTES or (before.st_size, before.st_mtime_ns) != (
        after.st_size,
        after.st_mtime_ns,
    ):
        raise ApprovedTaskDocumentError("Publish the approved task document as one complete file")
    try:
        decoded = json.loads(payload.decode("utf-8"), object_pairs_hook=_unique_fields)
        return ApprovedTaskBatch.model_validate(decoded).tasks
    except (ValueError, RecursionError):
        raise ApprovedTaskDocumentError(
            "The approved task document must contain a valid UTF-8 JSON task list"
        ) from None


def _unique_fields(pairs: list[tuple[str, object]]) -> dict[str, object]:
    fields = dict(pairs)
    if len(fields) != len(pairs):
        raise ValueError("Duplicate approved task document key")
    return fields
