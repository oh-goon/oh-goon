from __future__ import annotations

import zipfile
from pathlib import Path

from oh_goon.repository_tooling.release.exact_release_patch import build_exact_patch


def _zip(
    path: Path, files: dict[str, tuple[bytes, int]], *, root_name: str = "release"
) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for relative, (payload, mode) in sorted(files.items()):
            info = zipfile.ZipInfo(
                f"{root_name}/{relative}", date_time=(2020, 2, 2, 0, 0, 0)
            )
            info.external_attr = (mode & 0xFFFF) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, payload)


def test_exact_patch_detects_same_size_same_timestamp_content_change(
    tmp_path: Path,
) -> None:
    parent = tmp_path / "parent.zip"
    child = tmp_path / "child.zip"
    patch = tmp_path / "change.patch"
    _zip(parent, {"schema.txt": (b"82\n", 0o644), "keep.txt": (b"same\n", 0o644)})
    _zip(child, {"schema.txt": (b"84\n", 0o644), "keep.txt": (b"same\n", 0o644)})
    proof = build_exact_patch(parent, child, patch)
    assert proof.file_count == 2
    assert patch.read_bytes()
    assert "schema.txt" in patch.read_text(encoding="utf-8", errors="replace")


def test_exact_patch_replays_add_delete_and_executable_mode(tmp_path: Path) -> None:
    parent = tmp_path / "parent.zip"
    child = tmp_path / "child.zip"
    patch = tmp_path / "change.patch"
    _zip(parent, {"remove.txt": (b"old\n", 0o644), "tool.sh": (b"echo x\n", 0o644)})
    _zip(child, {"add.txt": (b"new\n", 0o644), "tool.sh": (b"echo x\n", 0o755)})
    proof = build_exact_patch(parent, child, patch)
    assert proof.file_count == 2
    assert patch.with_name(patch.name + ".sha256").is_file()


def test_exact_patch_normalizes_different_archive_top_level_roots(
    tmp_path: Path,
) -> None:
    parent = tmp_path / "parent.zip"
    child = tmp_path / "child.zip"
    patch = tmp_path / "change.patch"
    files = {"same.txt": (b"same\n", 0o644)}
    _zip(parent, files, root_name="oh-goon-parent")
    _zip(child, files, root_name="oh-goon-child")

    proof = build_exact_patch(parent, child, patch)

    assert proof.file_count == 1
    assert patch.read_bytes() == b""
