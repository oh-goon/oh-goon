"""CO-R3I: original LACE recovery must not bypass the native cgroup owner.

The local test filesystem emulates virtual cgroup control files; it does not
certify privileged parent mutation custody or atomic identity-conditional rmdir.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from oh_goon.integrations import _fleet_plan_cgroup_resources as cg
from oh_goon.integrations import fleet_plan_local_lace_native_reaper as reaper
from oh_goon.runtime.enforcer_lace_native_cleanup import NativeCleanupFailure
from tests.unit.integrations.test_b12d2c2_native_orphan_reaper import setup


def test_lace_reaper_uses_original_owner_and_preserves_journal_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease, subject, snapshot, native, ticket, kwargs, conn = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(reaper, "_process_identity", lambda _: None)
    original = reaper._remove_cgroup
    bound: list[tuple[int, int]] = []

    def inspect(path: Path, *, expected_identity: tuple[int, int]) -> None:
        bound.append(expected_identity)
        original(path, expected_identity=expected_identity)

    monkeypatch.setattr(reaper, "_remove_cgroup", inspect)
    with pytest.raises(NativeCleanupFailure) as failure:
        reaper.cleanup_local_orphan(native, ticket, subject, snapshot, **kwargs)
    assert failure.value.attempted == ()
    assert bound == []  # original owner refuses before native cgroup delete
    assert lease.state_file.is_file() and lease.cgroup.is_dir()
    assert not snapshot.native_absence_proven
    conn.close()


def test_lace_reaper_refuses_symlinked_kernel_observation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease, subject, snapshot, native, ticket, kwargs, conn = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(reaper, "_process_identity", lambda _: None)
    external = tmp_path / "external-events"
    external.write_text("populated 0\n", encoding="ascii")
    (lease.cgroup / "cgroup.events").unlink()
    (lease.cgroup / "cgroup.events").symlink_to(external)
    with pytest.raises(NativeCleanupFailure) as failure:
        reaper.cleanup_local_orphan(native, ticket, subject, snapshot, **kwargs)
    assert failure.value.attempted == ()
    assert lease.state_file.is_file() and lease.cgroup.is_dir()
    assert external.read_text(encoding="ascii") == "populated 0\n"
    conn.close()


def test_lace_reaper_refuses_nested_cgroup_without_removal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease, subject, snapshot, native, ticket, kwargs, conn = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(reaper, "_process_identity", lambda _: None)
    (lease.cgroup / "nested").mkdir()
    with pytest.raises(NativeCleanupFailure) as failure:
        reaper.cleanup_local_orphan(native, ticket, subject, snapshot, **kwargs)
    assert failure.value.attempted == ()
    assert (lease.cgroup / "nested").is_dir() and lease.state_file.is_file()
    conn.close()


def test_lace_reaper_cannot_ack_foreign_directory_deleted_at_rmdir_cut(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease, subject, snapshot, native, ticket, kwargs, conn = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(reaper, "_process_identity", lambda _: None)
    displaced = tmp_path / "original-still-linked"
    native_rmdir = os.rmdir
    raced = False

    def substitute_at_cut(path: str | os.PathLike[str], *args: object, **kwargs: object) -> None:
        nonlocal raced
        if path == lease.cgroup.name and "dir_fd" in kwargs and not raced:
            raced = True
            lease.cgroup.rename(displaced)
            lease.cgroup.mkdir()
        native_rmdir(path, *args, **kwargs)

    monkeypatch.setattr(cg.os, "rmdir", substitute_at_cut)
    with pytest.raises(NativeCleanupFailure) as failure:
        reaper.cleanup_local_orphan(native, ticket, subject, snapshot, **kwargs)
    assert not raced  # hostile rmdir-cut callback is never reached without OS custody
    assert failure.value.attempted == ()
    assert not displaced.exists() and lease.cgroup.is_dir()
    assert lease.state_file.is_file()
    conn.close()


def test_lace_reaper_rejects_unobservable_inode_ack_after_native_effect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease, subject, snapshot, native, ticket, kwargs, conn = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(reaper, "_process_identity", lambda _: None)
    actual_fstat = os.fstat

    def unavailable(fd: int) -> os.stat_result:
        if not lease.cgroup.exists():
            raise OSError("original kernel inode no longer observable")
        return actual_fstat(fd)

    monkeypatch.setattr(cg.os, "fstat", unavailable)
    with pytest.raises(NativeCleanupFailure) as failure:
        reaper.cleanup_local_orphan(native, ticket, subject, snapshot, **kwargs)
    assert failure.value.attempted == ()
    assert lease.cgroup.is_dir() and lease.state_file.is_file()
    conn.close()
