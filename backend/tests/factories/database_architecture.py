"""Current SQLite source owners for standalone architecture test replicas."""

from __future__ import annotations

from pathlib import Path
from shutil import copyfile

from oh_goon.repository_tooling.architecture.repository_layout import ROOT


def copy_database_architecture_sources(destination: Path) -> Path:
    """Copy the actual current ownership contract without checkpoint metadata."""
    package = destination / "backend/src/oh_goon"
    for relative in (
        "runtime/database.py",
        "runtime/enforcer_lace_signer_trust_epoch.py",
        "runtime/enforcer_lace_journal.py",
        "services/fleet_admission.py",
        "scout/database.py",
    ):
        target = package / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        copyfile(ROOT / "backend/src/oh_goon" / relative, target)
    return package
