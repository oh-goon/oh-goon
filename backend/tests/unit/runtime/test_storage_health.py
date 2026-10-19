from __future__ import annotations

import errno
import sqlite3
from pathlib import Path

import pytest

from oh_goon.runtime.storage_health import probe_writable_directory, storage_unavailable_reason


def test_storage_unavailable_reason_accepts_only_concrete_storage_failures() -> None:
    assert "Read-only file system" in storage_unavailable_reason(
        OSError(errno.EROFS, "Read-only file system")
    )
    assert "Input/output error" in storage_unavailable_reason(
        OSError(errno.EIO, "Input/output error")
    )
    assert (
        storage_unavailable_reason(sqlite3.OperationalError("unable to open database file"))
        == "unable to open database file"
    )
    assert storage_unavailable_reason(sqlite3.OperationalError("no such table: nope")) is None
    assert storage_unavailable_reason(ValueError("application defect")) is None


def test_probe_writable_directory_creates_no_retained_evidence(tmp_path: Path) -> None:
    probe_writable_directory(tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_probe_writable_directory_fails_closed_when_owner_cannot_create(
    monkeypatch, tmp_path: Path
) -> None:
    def fail_open(*_args, **_kwargs):
        raise OSError(errno.EROFS, "Read-only file system")

    monkeypatch.setattr("oh_goon.runtime.storage_health.os.open", fail_open)
    with pytest.raises(OSError) as exc:
        probe_writable_directory(tmp_path)
    assert exc.value.errno == errno.EROFS


def test_storage_capacity_policy_rejects_inverted_floor() -> None:
    from oh_goon.runtime.storage_health import resolve_storage_capacity_policy

    with pytest.raises(ValueError, match="must be <="):
        resolve_storage_capacity_policy(
            {
                "OH_GOON_STORAGE_SOFT_FREE_BYTES": "100",
                "OH_GOON_STORAGE_HARD_FREE_BYTES": "101",
            }
        )


def test_storage_capacity_hard_floor_blocks_new_heavy_materialization(
    tmp_path: Path, monkeypatch
) -> None:
    from collections import namedtuple

    from oh_goon.runtime.storage_health import StorageCapacityPolicy, require_storage_capacity

    Usage = namedtuple("Usage", "total used free")
    monkeypatch.setattr(
        "oh_goon.runtime.storage_health.shutil.disk_usage",
        lambda _path: Usage(total=1000, used=950, free=50),
    )

    with pytest.raises(OSError, match="storage safety floor") as caught:
        require_storage_capacity(
            tmp_path, StorageCapacityPolicy(soft_free_bytes=200, hard_free_bytes=100)
        )
    assert caught.value.errno == errno.ENOSPC


def test_storage_capacity_soft_pressure_does_not_fake_hard_failure(
    tmp_path: Path, monkeypatch
) -> None:
    from collections import namedtuple

    from oh_goon.runtime.storage_health import StorageCapacityPolicy, require_storage_capacity

    Usage = namedtuple("Usage", "total used free")
    monkeypatch.setattr(
        "oh_goon.runtime.storage_health.shutil.disk_usage",
        lambda _path: Usage(total=1000, used=850, free=150),
    )

    observed = require_storage_capacity(
        tmp_path, StorageCapacityPolicy(soft_free_bytes=200, hard_free_bytes=100)
    )
    assert observed.pressure == "SOFT_PRESSURE"
    assert observed.free_bytes == 150
