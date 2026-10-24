from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from oh_goon.repository_tooling.self_certification_artifacts import (
    verify_fresh_source_zip,
)
from oh_goon.repository_tooling.release import package_slim


@pytest.fixture
def fresh_source_archive(tmp_path: Path) -> tuple[Path, Path, str, str]:
    root = tmp_path / "source"
    files = {
        "VERSION": "1.2.3\n",
        "README.md": "# Canonical archived content\n",
        "backend/pyproject.toml": '[project]\nname="demo"\nversion="1.2.3"\n',
        "backend/uv.lock": 'version=1\nrequires-python=">=3.12"\npackage=[]\n',
    }
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    identity = package_slim.canonical_source_identity(root)
    archive = tmp_path / "oh-goon-1.2.3.zip"
    digest = package_slim.package_slim(archive, root=root)
    return root, archive, digest, identity


def test_fresh_source_archive_retains_real_source_identity_and_committed_lock_bytes(
    tmp_path: Path, fresh_source_archive: tuple[Path, Path, str, str]
) -> None:
    root, archive, digest, identity = fresh_source_archive
    receipt = verify_fresh_source_zip(
        root, tmp_path / "evidence", archive, digest, identity, "fixture-seal"
    )
    evidence = json.loads(receipt.read_text(encoding="utf-8"))
    assert evidence["status"] == "PASS"
    assert evidence["archive_source_identity"] == identity
    assert evidence["source_zip_sha256"] == digest
    assert (
        evidence["dependency_scope"]["backend_lock_sha256"]
        == hashlib.sha256((root / "backend/uv.lock").read_bytes()).hexdigest()
    )
    with zipfile.ZipFile(archive) as native:
        assert native.testzip() is None
        assert (
            native.read("oh-goon-1.2.3/backend/uv.lock")
            == (root / "backend/uv.lock").read_bytes()
        )


@pytest.mark.parametrize(
    ("tamper", "message"),
    [
        ("archive-bytes", "source ZIP changed"),
        ("sidecar", "checksum sidecar"),
        ("payload", "payload identity differs"),
        ("missing-lock", "dependency authorities are missing"),
        ("crc", "source ZIP integrity failed"),
    ],
)
def test_fresh_source_archive_rejects_byte_identity_lock_and_crc_tampering(
    tmp_path: Path,
    fresh_source_archive: tuple[Path, Path, str, str],
    tamper: str,
    message: str,
) -> None:
    root, archive, digest, identity = fresh_source_archive
    sidecar = archive.with_name(archive.name + ".sha256")
    if tamper == "archive-bytes":
        with archive.open("ab") as stream:
            stream.write(b"unadmitted appended bytes")
    elif tamper == "sidecar":
        sidecar.write_text("0" * 64 + "  " + archive.name + "\n", encoding="utf-8")
    else:
        with zipfile.ZipFile(archive) as native:
            members = [(info, native.read(info)) for info in native.infolist()]
        with zipfile.ZipFile(archive, "w") as modified:
            for info, data in members:
                if tamper == "missing-lock" and info.filename.endswith(
                    "/backend/uv.lock"
                ):
                    continue
                if tamper == "payload" and info.filename.endswith("/README.md"):
                    data = b"Changed archived source\n"
                if tamper == "crc":
                    info.compress_type = zipfile.ZIP_STORED
                modified.writestr(info, data)
        if tamper == "crc":
            # Corrupt a stored member while leaving the native ZIP CRC unchanged.
            marker = (root / "README.md").read_bytes()
            payload = bytearray(archive.read_bytes())
            assert payload.count(marker) == 1
            payload[payload.index(marker)] ^= 1
            archive.write_bytes(payload)
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        sidecar.write_text(f"{digest}  {archive.name}\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match=message):
        verify_fresh_source_zip(
            root, tmp_path / "evidence", archive, digest, identity, "fixture-seal"
        )


@pytest.mark.parametrize(
    ("prefix", "version", "message"),
    [
        (
            "banana",
            "1.2.3",
            "top-level directory must carry canonical Oh-Goon version identity",
        ),
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
        info.external_attr = 0o120777 << 16
        archive.writestr(info, "../../outside.py")

    with pytest.raises(ValueError, match="non-regular file member"):
        package_slim.archive_source_identity(archive_path)


@pytest.mark.parametrize(
    ("member", "message"),
    [
        ("repositories/local/README.md", "forbidden transient or local-only payload"),
        ("unknown/README.md", "unowned top-level entry"),
        ("frontend/coverage/summary.json", "forbidden transient or local-only payload"),
        (
            "frontend/playwright-report/index.html",
            "forbidden transient or local-only payload",
        ),
        (
            "frontend/playwright-runtime-report/index.html",
            "forbidden transient or local-only payload",
        ),
        (
            "frontend/test-results/result.txt",
            "forbidden transient or local-only payload",
        ),
    ],
)
def test_archive_source_identity_admits_only_release_source_members(
    tmp_path: Path, member: str, message: str
) -> None:
    root = tmp_path / "source"
    source = root / member
    source.parent.mkdir(parents=True)
    source.write_text("payload\n", encoding="utf-8")
    if member.startswith("unknown/"):
        with pytest.raises(ValueError, match="unowned top-level entries"):
            package_slim.canonical_source_files(root)
    else:
        assert source not in package_slim.canonical_source_files(root)

    archive_path = tmp_path / "source.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("oh-goon-1.2.3/VERSION", "1.2.3\n")
        archive.writestr(f"oh-goon-1.2.3/{member}", "payload\n")

    with pytest.raises(ValueError, match=message):
        package_slim.archive_source_identity(archive_path)


@pytest.mark.parametrize(
    "members", [("docs", "docs/a.md"), ("docs/Guide", "docs/guide/a.md")]
)
@pytest.mark.parametrize("reverse_order", [False, True])
def test_archive_source_identity_rejects_file_ancestor_collisions_in_any_member_order(
    tmp_path: Path, members: tuple[str, str], reverse_order: bool
) -> None:
    archive_path = tmp_path / "source.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("oh-goon-1.2.3/VERSION", "1.2.3\n")
        for member in reversed(members) if reverse_order else members:
            archive.writestr(f"oh-goon-1.2.3/{member}", "regular file\n")

    with pytest.raises(ValueError, match="file/ancestor path collision"):
        package_slim.archive_source_identity(archive_path)


@pytest.mark.parametrize(
    ("directory", "file_member"),
    [
        ("oh-goon-1.2.3/docs/", "docs"),
        ("oh-goon-1.2.3/docs/empty/", None),
        ("oh-goon-1.2.3/../outside/", None),
        ("other/docs/", None),
    ],
)
def test_archive_source_identity_refuses_explicit_directory_members(
    tmp_path: Path, directory: str, file_member: str | None
) -> None:
    archive_path = tmp_path / "source.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("oh-goon-1.2.3/VERSION", "1.2.3\n")
        if file_member is not None:
            archive.writestr(f"oh-goon-1.2.3/{file_member}", "regular file\n")
        archive.writestr(directory, "")

    with pytest.raises(
        ValueError, match="explicit directory members are not canonical"
    ):
        package_slim.archive_source_identity(archive_path)
