from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path


from oh_goon.core.file_digest import sha256_file

@dataclass(frozen=True)

class ExactPatchProof:
    patch_sha256: str
    file_count: int


def _run(*argv: str, cwd: Path) -> bytes:
    result = subprocess.run(argv, cwd=cwd, check=False, capture_output=True)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or b"command failed").decode(errors="replace")
        raise RuntimeError(f"{argv[0]} failed: {detail.strip()}")
    return result.stdout


def _extract_single_root(archive_path: Path, destination: Path) -> Path:
    with zipfile.ZipFile(archive_path) as archive:
        archive.extractall(destination)
    roots = [path for path in destination.iterdir() if path.is_dir()]
    if len(roots) != 1 or any(path.is_file() for path in destination.iterdir()):
        raise ValueError("release ZIP must contain exactly one top-level directory")
    return roots[0]


def _inventory(root: Path) -> dict[str, tuple[str, int, int]]:
    result: dict[str, tuple[str, int, int]] = {}
    for path in sorted(root.rglob("*")):
        if ".git" in path.parts:
            continue
        if path.is_symlink():
            raise ValueError(f"exact patch refuses symlink: {path}")
        if path.is_file():
            stat_result = path.stat()
            result[path.relative_to(root).as_posix()] = (
                sha256_file(path),
                stat_result.st_mode & 0o777,
                stat_result.st_size,
            )
    return result


def _sync_exact(source: Path, destination: Path) -> None:
    """Make destination equal source by content/mode, never timestamp/size quick checks."""

    source_files = _inventory(source)
    destination_files = _inventory(destination)
    for relative in sorted(set(destination_files) - set(source_files), reverse=True):
        (destination / relative).unlink()
    for relative, source_identity in source_files.items():
        _digest, mode, _byte_count = source_identity
        target = destination / relative
        current = destination_files.get(relative)
        if current != source_identity:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / relative, target)
            os.chmod(target, mode)
    for directory in sorted(
        (path for path in destination.rglob("*") if path.is_dir() and ".git" not in path.parts),
        key=lambda path: len(path.parts),
        reverse=True,
    ):
        if not any(directory.iterdir()):
            directory.rmdir()


def _init_parent(root: Path) -> None:
    _run("git", "init", "-q", cwd=root)
    _run("git", "config", "user.email", "exact-release-patch@example.invalid", cwd=root)
    _run("git", "config", "user.name", "Oh-Goon Exact Release Patch", cwd=root)
    _run("git", "add", "-A", cwd=root)
    _run("git", "commit", "-qm", "exact parent", cwd=root)


def _assert_exact(expected: Path, observed: Path) -> int:
    expected_files = _inventory(expected)
    observed_files = _inventory(observed)
    if expected_files.keys() != observed_files.keys():
        missing = sorted(expected_files.keys() - observed_files.keys())
        extra = sorted(observed_files.keys() - expected_files.keys())
        raise RuntimeError(f"exact patch replay path mismatch: missing={missing[:5]}, extra={extra[:5]}")
    changed = [name for name in expected_files if expected_files[name] != observed_files[name]]
    if changed:
        raise RuntimeError(f"exact patch replay byte/mode mismatch: {changed[:5]}")
    return len(expected_files)


def build_exact_patch(parent_zip: Path, child_zip: Path, output_patch: Path) -> ExactPatchProof:
    parent_zip = parent_zip.resolve()
    child_zip = child_zip.resolve()
    output_patch = output_patch.resolve()
    if output_patch.is_symlink():
        raise ValueError("exact patch output must not be a symlink")
    output_patch.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="oh-goon-exact-patch-") as temporary:
        work = Path(temporary)
        parent = _extract_single_root(parent_zip, work / "parent")
        child = _extract_single_root(child_zip, work / "child")
        replay = _extract_single_root(parent_zip, work / "replay")

        _init_parent(parent)
        _sync_exact(child, parent)
        _run("git", "add", "-A", cwd=parent)
        patch = _run("git", "diff", "--cached", "--binary", "--full-index", "HEAD", cwd=parent)
        output_patch.write_bytes(patch)

        _init_parent(replay)
        if patch:
            result = subprocess.run(
                ("git", "apply", "--index", str(output_patch)),
                cwd=replay,
                check=False,
                capture_output=True,
            )
            if result.returncode != 0:
                detail = (result.stderr or result.stdout).decode(errors="replace")
                raise RuntimeError(f"exact patch replay failed: {detail.strip()}")
        # An empty patch is a valid exact proof when archive roots differ but their
        # normalized payloads are byte/mode identical. git apply rejects an empty
        # file, so exact inventory comparison remains the authoritative replay proof.
        file_count = _assert_exact(child, replay)

    digest = sha256_file(output_patch)
    output_patch.with_name(output_patch.name + ".sha256").write_text(
        f"{digest}  {output_patch.name}\n", encoding="utf-8"
    )
    return ExactPatchProof(patch_sha256=digest, file_count=file_count)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build and independently replay an exact Oh-Goon release patch")
    parser.add_argument("parent_zip", type=Path)
    parser.add_argument("child_zip", type=Path)
    parser.add_argument("output_patch", type=Path)
    args = parser.parse_args()
    proof = build_exact_patch(args.parent_zip, args.child_zip, args.output_patch)
    print(f"EXACT PATCH PASS files={proof.file_count} sha256={proof.patch_sha256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
