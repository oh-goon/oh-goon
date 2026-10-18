from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.integrations import content_snapshot
from oh_goon.integrations.content_snapshot import replace_tree_from_tar_gz


def _archive(*members: tuple[tarfile.TarInfo, bytes]) -> bytes:
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        for member, payload in members:
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))
    return stream.getvalue()


def test_archive_restore_rejects_parent_traversal_without_replacing_destination(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "workspace"
    destination.mkdir()
    (destination / "kept.txt").write_text("kept", encoding="utf-8")
    escaping = tarfile.TarInfo("../foreign.txt")

    with pytest.raises(FleetPlanInvalid, match="must not contain '..' traversal"):
        replace_tree_from_tar_gz(
            _archive((escaping, b"foreign")),
            destination,
            label="remote result",
        )

    assert (destination / "kept.txt").read_text(encoding="utf-8") == "kept"
    assert not (tmp_path / "foreign.txt").exists()


def test_archive_restore_rejects_duplicate_paths(tmp_path: Path) -> None:
    first = tarfile.TarInfo("result.txt")
    second = tarfile.TarInfo("result.txt")

    with pytest.raises(FleetPlanInvalid, match="duplicate path"):
        replace_tree_from_tar_gz(
            _archive((first, b"first"), (second, b"second")),
            tmp_path / "workspace",
            label="remote result",
        )


def test_archive_restore_accepts_owned_tree_and_publishes_validated_tree(tmp_path: Path) -> None:
    destination = tmp_path / "workspace"
    destination.mkdir()
    (destination / "old.txt").write_text("old", encoding="utf-8")
    member = tarfile.TarInfo("result.txt")

    identity = replace_tree_from_tar_gz(
        _archive((member, b"result")),
        destination,
        label="remote result",
    )

    assert not (destination / "old.txt").exists()
    assert (destination / "result.txt").read_bytes() == b"result"
    assert identity.byte_count == len(b"result")


def test_archive_restore_rejects_hard_link_members(tmp_path: Path) -> None:
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        original = tarfile.TarInfo("original.txt")
        original.size = len(b"secret")
        archive.addfile(original, io.BytesIO(b"secret"))
        alias = tarfile.TarInfo("alias.txt")
        alias.type = tarfile.LNKTYPE
        alias.linkname = "original.txt"
        archive.addfile(alias)

    with pytest.raises(FleetPlanInvalid, match="hard-link ownership"):
        replace_tree_from_tar_gz(
            stream.getvalue(),
            tmp_path / "workspace",
            label="remote result",
        )


def test_archive_restore_keeps_destination_when_displacement_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "workspace"
    destination.mkdir()
    (destination / "old.txt").write_text("old", encoding="utf-8")
    member = tarfile.TarInfo("result.txt")
    real_replace = content_snapshot._replace_path

    def fail_displacement(source: Path, target: Path) -> None:
        if source == destination:
            raise OSError("cannot displace existing workspace")
        real_replace(source, target)

    monkeypatch.setattr(content_snapshot, "_replace_path", fail_displacement)

    with pytest.raises(FleetPlanInvalid, match="cannot displace existing workspace"):
        replace_tree_from_tar_gz(
            _archive((member, b"result")),
            destination,
            label="remote result",
        )

    assert (destination / "old.txt").read_text(encoding="utf-8") == "old"
    assert not (destination / "result.txt").exists()
    assert list(tmp_path.glob(".fleet-restore-*")) == []


def test_archive_restore_restores_destination_when_new_tree_publication_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "workspace"
    destination.mkdir()
    (destination / "old.txt").write_text("old", encoding="utf-8")
    member = tarfile.TarInfo("result.txt")
    real_replace = content_snapshot._replace_path
    calls = 0

    def fail_new_tree(source: Path, target: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("cannot publish prepared workspace")
        real_replace(source, target)

    monkeypatch.setattr(content_snapshot, "_replace_path", fail_new_tree)

    with pytest.raises(FleetPlanInvalid, match="previous destination was restored"):
        replace_tree_from_tar_gz(
            _archive((member, b"result")),
            destination,
            label="remote result",
        )

    assert (destination / "old.txt").read_text(encoding="utf-8") == "old"
    assert not (destination / "result.txt").exists()
    assert list(tmp_path.glob(".fleet-restore-*")) == []


def test_archive_restore_preserves_backup_when_publication_and_restore_both_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "workspace"
    destination.mkdir()
    (destination / "old.txt").write_text("old", encoding="utf-8")
    member = tarfile.TarInfo("result.txt")
    real_replace = content_snapshot._replace_path
    calls = 0

    def fail_publication_and_restore(source: Path, target: Path) -> None:
        nonlocal calls
        calls += 1
        if calls in {2, 3}:
            raise OSError("rename unavailable")
        real_replace(source, target)

    monkeypatch.setattr(content_snapshot, "_replace_path", fail_publication_and_restore)

    with pytest.raises(FleetPlanInvalid, match="previous destination remains at"):
        replace_tree_from_tar_gz(
            _archive((member, b"result")),
            destination,
            label="remote result",
        )

    assert not destination.exists()
    backups = list(tmp_path.glob(".fleet-restore-backup-*"))
    assert len(backups) == 1
    assert (backups[0] / "old.txt").read_text(encoding="utf-8") == "old"
