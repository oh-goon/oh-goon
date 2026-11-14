"""The Iris application's bounded captured resource, not a host data selector."""

from __future__ import annotations

import hashlib
import json
import math
import os
import stat
from contextlib import ExitStack
from pathlib import Path
from typing import Any

MAX_DATASET_BYTES = 64 * 1024
MAX_RECORD_ID_BYTES = 256


class DatasetError(Exception):
    code = "IRIS_DATASET_INVALID"


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, value in pairs:
        if name in result:
            raise DatasetError
        result[name] = value
    return result


def _nonfinite(_value: str) -> Any:
    raise DatasetError


def _resource_bytes() -> bytes:
    required = ("O_DIRECTORY", "O_NOFOLLOW", "O_NONBLOCK", "O_CLOEXEC")
    if not all(hasattr(os, name) for name in required):
        raise DatasetError
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
    try:
        with ExitStack() as resources:
            package = os.open(Path(__file__).parent, flags | os.O_DIRECTORY)
            resources.callback(os.close, package)
            directory = os.open("data", flags | os.O_DIRECTORY, dir_fd=package)
            resources.callback(os.close, directory)
            source = os.open("training.json", flags, dir_fd=directory)
            resources.callback(os.close, source)
            metadata = os.fstat(source)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_size > MAX_DATASET_BYTES
            ):
                raise DatasetError
            with os.fdopen(source, "rb", closefd=False) as stream:
                payload = stream.read(MAX_DATASET_BYTES + 1)
            if len(payload) > MAX_DATASET_BYTES:
                raise DatasetError
        return payload
    except (OSError, UnicodeError, NotImplementedError):
        raise DatasetError from None


def load_captured_dataset() -> tuple[list[list[float]], list[int], str]:
    """Read the one captured application resource and validate before fitting."""
    payload = _resource_bytes()
    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_nonfinite,
        )
        if (
            not isinstance(value, dict)
            or not {"data", "target"} <= value.keys()
            or value.keys() - {"data", "target", "record_ids"}
        ):
            raise DatasetError
        rows, target = value["data"], value["target"]
        if not isinstance(rows, list) or len(rows) < 30:
            raise DatasetError
        if not isinstance(target, list) or len(target) != len(rows):
            raise DatasetError
        if any(type(label) is not int or label not in (0, 1, 2) for label in target):
            raise DatasetError
        data: list[list[float]] = []
        for row in rows:
            if not isinstance(row, list) or len(row) != 4:
                raise DatasetError
            if any(
                type(number) not in (int, float) or not math.isfinite(number)
                for number in row
            ):
                raise DatasetError
            data.append([float(number) for number in row])
        if "record_ids" in value:
            records = value["record_ids"]
            if (
                not isinstance(records, list)
                or len(records) != len(rows)
                or any(
                    not isinstance(record, str)
                    or len(record.encode("utf-8")) > MAX_RECORD_ID_BYTES
                    for record in records
                )
            ):
                raise DatasetError
        return data, target, hashlib.sha256(payload).hexdigest()
    except (ValueError, TypeError, OverflowError, RecursionError):
        raise DatasetError from None
