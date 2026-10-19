from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

_ARCHITECTURE_MODULE = "oh_goon.repository_tooling.check_architecture"
_GENERATED_MODULE = "oh_goon.repository_tooling.generation.check_generated"
_GOON_ADMISSION_MODULE = "oh_goon.repository_tooling.goon_static_admission"
_PHASE_EVIDENCE_MODULE = "oh_goon.repository_tooling.phase_evidence_convergence"


def _module_environment(root: Path) -> dict[str, str]:
    environment = os.environ.copy()
    # Seal checks must import only from the exact source tree being admitted.
    # Inheriting caller PYTHONPATH would let host modules contaminate packaged replay.
    environment["PYTHONPATH"] = str(root / "backend" / "src")
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return environment


def _run_module(root: Path, module: str, *args: str) -> None:
    result = subprocess.run(
        (sys.executable, "-m", module, *args),
        cwd=root,
        env=_module_environment(root),
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return
    detail = (result.stderr or result.stdout or f"{module} failed").strip()
    raise RuntimeError(f"checkpoint seal gate failed in {module}: {detail}")


def require_checkpoint_version_freshness(root: Path) -> None:
    version = (root / "VERSION").read_text(encoding="utf-8").strip()
    try:
        checkpoint = (root / "CHECKPOINT_NOT_RELEASE.md").read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(
            "checkpoint seal requires local archive metadata CHECKPOINT_NOT_RELEASE.md; "
            "restore the verified checkpoint handoff before archive sealing. "
            "Ordinary source development uses make init and make check."
        ) from exc
    checkpoint_lines = checkpoint.splitlines()
    if len(checkpoint_lines) >= 60:
        raise RuntimeError(
            "checkpoint seal gate refuses embedded phase history; "
            f"CHECKPOINT_NOT_RELEASE.md has {len(checkpoint_lines)} lines (must be < 60)"
        )
    expected_heading = f"## Current development checkpoint: {version}"
    status_lines = [
        line for line in checkpoint.splitlines() if line.startswith("Current status: **")
    ]
    if expected_heading not in checkpoint:
        raise RuntimeError(
            "checkpoint seal gate found stale current-checkpoint heading: "
            f"expected {expected_heading!r}"
        )
    if len(status_lines) != 1 or version not in status_lines[0]:
        raise RuntimeError(
            "checkpoint seal gate found stale current-status version; "
            f"VERSION={version!r} status_lines={status_lines!r}"
        )


def require_source_seal_gate(root: Path) -> None:
    """Run every source-side authority required immediately before packaging."""

    root = root.resolve()
    require_checkpoint_version_freshness(root)
    _run_module(root, _PHASE_EVIDENCE_MODULE)
    _run_module(root, _ARCHITECTURE_MODULE)
    _run_module(root, _GENERATED_MODULE)
    _run_module(root, _GOON_ADMISSION_MODULE, "--root", str(root))


def _archive_root(archive_path: Path, destination: Path) -> Path:
    with zipfile.ZipFile(archive_path) as archive:
        names = [info.filename for info in archive.infolist()]
        if not names:
            raise ValueError("source archive is empty")
        roots: set[str] = set()
        for name in names:
            member = PurePosixPath(name)
            if (
                member.is_absolute()
                or not member.parts
                or any(part in {"", ".", ".."} for part in member.parts)
            ):
                raise ValueError(f"unsafe source archive member: {name}")
            roots.add(member.parts[0])
        if len(roots) != 1:
            raise ValueError("source archive must contain exactly one top-level directory")
        archive.extractall(destination)
    root = destination / next(iter(roots))
    if not root.is_dir():
        raise ValueError("source archive top-level member must be a directory")
    return root


def require_packaged_architecture_gate(archive_path: Path) -> None:
    """Re-run the complete architecture authority from the exact packaged bytes."""

    with tempfile.TemporaryDirectory(prefix="oh-goon-packaged-architecture-") as directory:
        root = _archive_root(archive_path.resolve(), Path(directory))
        _run_module(root, _ARCHITECTURE_MODULE)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run one isolated Oh-Goon release checkpoint seal gate"
    )
    parser.add_argument(
        "--packaged-architecture",
        type=Path,
        required=True,
        help="exact source ZIP whose packaged architecture must be admitted",
    )
    args = parser.parse_args(argv)
    require_packaged_architecture_gate(args.packaged_architecture)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
