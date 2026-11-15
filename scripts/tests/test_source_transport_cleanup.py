from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from oh_goon.repository_tooling import source_layout

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(os.name != "posix", reason="native Linux/WSL cleanup")


def _checkout(root: Path) -> None:
    (root / "scripts").mkdir(parents=True)
    (root / "backend").mkdir()
    (root / "VERSION").write_text("test\n", encoding="utf-8")
    (root / "backend/pyproject.toml").write_text(
        "[project]\nname='test'\n", encoding="utf-8"
    )
    shutil.copyfile(ROOT / "Makefile", root / "Makefile")
    shutil.copyfile(
        ROOT / "scripts/source_transport_clean.sh",
        root / "scripts/source_transport_clean.sh",
    )
    owner = ROOT / "backend/src/oh_goon/repository_tooling"
    for resource in owner.rglob("*.sh"):
        target = root / resource.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(resource, target)


def _clean(root: Path, entrypoint: str, monkeypatch: pytest.MonkeyPatch) -> int:
    if entrypoint == "python":
        monkeypatch.setattr(source_layout, "ROOT", root)
        return source_layout.main(["transport-clean"])
    result = subprocess.run(
        ("make", "source-transport-clean"),
        cwd=root,
        capture_output=True,
        text=True,
        timeout=15,
    )
    return result.returncode


@pytest.mark.parametrize("entrypoint", ["make", "python"])
def test_cleanup_frontdoors_remove_only_valid_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entrypoint: str
) -> None:
    root = tmp_path / "checkout with [spaces]"
    _checkout(root)
    subject = root / "source name\nwith spaces.py"
    subject.write_bytes(b"authored\n")
    sidecar = subject.with_name(subject.name + ":Zone.Identifier:$DATA")
    sidecar.write_bytes(b"\xff\xfeopaque Windows data")
    patch = root / "repair name\n.patch"
    patch.write_bytes(b"transport")
    checksum = patch.with_name(patch.name + ".sha256")
    checksum.write_bytes(b"transport checksum")
    directory = root / "directory"
    directory.mkdir()
    directory_sidecar = root / "directory:Zone.Identifier"
    directory_sidecar.write_bytes(b"")

    assert _clean(root, entrypoint, monkeypatch) == 0
    assert subject.read_bytes() == b"authored\n" and directory.is_dir()
    assert not sidecar.exists() and not patch.exists() and not checksum.exists()
    assert not directory_sidecar.exists()
    assert _clean(root, entrypoint, monkeypatch) == 0


@pytest.mark.parametrize("entrypoint", ["make", "python"])
def test_cleanup_preserves_unknown_names_and_materialized_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entrypoint: str
) -> None:
    root = tmp_path / "checkout"
    _checkout(root)
    preserved = []
    for name in (
        ".patch",
        ".patch.sha256",
        ":Zone.Identifier",
        ":Zone.Identifier:$DATA",
        "file:UnknownData",
    ):
        path = root / name
        path.write_bytes(b"keep")
        preserved.append(path)
    directories = {Path(path) for path in source_layout.TRANSIENT_RELATIVE_PATHS}
    directories.update(Path(name) for name in source_layout.TRANSIENT_DIRECTORY_NAMES)
    directories.update((Path("metadata.egg-info"), Path("metadata.dist-info")))
    for directory in directories:
        path = root / directory / "retained.patch"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"keep")
        preserved.append(path)
    assert _clean(root, entrypoint, monkeypatch) == 0
    assert all(path.read_bytes() == b"keep" for path in preserved)


@pytest.mark.parametrize("entrypoint", ["make", "python"])
@pytest.mark.parametrize(
    "invalid",
    [
        "orphan",
        "candidate-symlink",
        "candidate-hardlink",
        "owner-symlink",
        "owner-hardlink",
        "late-symlink",
        "late-directory",
    ],
)
def test_cleanup_frontdoors_reject_complete_invalid_inventory_before_removal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entrypoint: str, invalid: str
) -> None:
    root = tmp_path / "checkout"
    _checkout(root)
    valid = root / "first.patch"
    valid.write_bytes(b"transport")
    outside = tmp_path / "outside"
    outside.write_bytes(b"outside stays unchanged")
    owner = root / "source.py"
    candidate = root / "source.py:Zone.Identifier"
    if invalid == "owner-symlink":
        owner.symlink_to(outside)
    elif invalid == "owner-hardlink":
        owner.hardlink_to(outside)
    elif invalid != "orphan":
        owner.write_bytes(b"authored")
    if invalid == "candidate-symlink":
        candidate.symlink_to(outside)
    elif invalid == "candidate-hardlink":
        candidate.hardlink_to(outside)
    elif invalid.startswith("late-"):
        candidate = root / "later" / "invalid.patch"
        candidate.parent.mkdir()
        if invalid == "late-symlink":
            candidate.symlink_to(outside)
        else:
            candidate.mkdir()
    else:
        candidate.write_bytes(b"transport")

    assert _clean(root, entrypoint, monkeypatch) == 2
    assert valid.read_bytes() == b"transport"
    assert candidate.exists() and outside.read_bytes() == b"outside stays unchanged"
