from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from oh_goon.repository_tooling.release import package_slim

@pytest.mark.parametrize(
    ("prefix", "version", "message"),
    [
        ("banana", "1.2.3", "top-level directory must carry canonical Oh-Goon version identity"),
        ("oh-goon-1.2.3", "1.2.4", "VERSION does not match top-level version identity"),
    ],
)
def test_archive_source_identity_binds_archive_name_to_version_payload(
    tmp_path: Path, prefix: str, version: str, message: str
) -> None:
    archive_path = tmp_path / "source-version-identity.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(f"{prefix}/VERSION", version + "\n")

    with pytest.raises(ValueError, match=message):
        package_slim.archive_source_identity(archive_path)


def test_archive_source_identity_requires_version_member(tmp_path: Path) -> None:
    archive_path = tmp_path / "source-no-version.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("oh-goon-1.2.3/README.md", "hello\n")

    with pytest.raises(ValueError, match="exactly one canonical VERSION member"):
        package_slim.archive_source_identity(archive_path)


@pytest.mark.parametrize(
    "member",
    [
        "oh-goon-1.2.3/./VERSION",
        r"oh-goon-1.2.3/backend\\source.py",
    ],
)
def test_archive_source_identity_refuses_noncanonical_member_spelling(
    tmp_path: Path, member: str
) -> None:
    archive_path = tmp_path / "source-noncanonical-member.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(member, "1.2.3\n")
        if not member.endswith("VERSION"):
            archive.writestr("oh-goon-1.2.3/VERSION", "1.2.3\n")

    with pytest.raises(ValueError, match="canonical"):
        package_slim.archive_source_identity(archive_path)


def test_archive_source_identity_refuses_symlink_member(tmp_path: Path) -> None:
    archive_path = tmp_path / "source-symlink.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("oh-goon-1.2.3/VERSION", "1.2.3\n")
        info = zipfile.ZipInfo("oh-goon-1.2.3/backend/link.py")
        info.create_system = 3
        info.external_attr = (0o120777 << 16)
        archive.writestr(info, "../../outside.py")

    with pytest.raises(ValueError, match="non-regular file member"):
        package_slim.archive_source_identity(archive_path)
