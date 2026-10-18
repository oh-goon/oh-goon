"""Current source closure for the existing execution-ownership architecture gate."""

from __future__ import annotations

from pathlib import Path
from shutil import copyfile, copytree, ignore_patterns

from oh_goon.repository_tooling.architecture.repository_layout import ROOT


def copy_x0_architecture_sources(destination: Path) -> Path:
    """Copy authored gate inputs, excluding environments and archival enrollment."""
    package = destination / "backend/src/oh_goon"
    copytree(
        ROOT / "backend/src/oh_goon",
        package,
        dirs_exist_ok=True,
        ignore=ignore_patterns("__pycache__", "*.pyc"),
    )
    for relative in (
        "backend/pyproject.toml",
        "docs/architecture/ADR_AGENT_WORKLOAD_CANONICAL_EXECUTION.md",
        "scripts/qualify_b12_development_wsl.sh",
        "backend/tests/native/test_p1b27b2b3b2b3a_effectful_mount.py",
        "backend/tests/native/test_co_r3u2b2b2a_native_refusal.py",
    ):
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        copyfile(ROOT / relative, target)
    return package
