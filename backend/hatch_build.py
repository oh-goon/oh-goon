"""Consume admitted release payloads at the native Hatch build boundary.

The repository source and editable developer environment do not contain a built
frontend.  Only a standard release wheel is allowed to consume
``generated/release/static``.  Keeping that mapping here means native editable
materialization never depends on a generated release directory, while release
wheel construction fails closed if its already-built UI or captured catalog
delivery is missing. The producer owns temporary catalog storage and cleanup.
"""

from __future__ import annotations

import hashlib
import os
import tarfile
from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

RELEASE_STATIC_TARGET = "oh_goon/static"


def release_static_inclusion(*, project_root: Path, version: str) -> dict[str, str]:
    """Return the release UI mapping for a standard wheel and nothing else."""

    if version != "standard":
        return {}
    static_root = project_root.parent / "generated" / "release" / "static"
    index = static_root / "index.html"
    if not index.is_file():
        raise RuntimeError(
            "standard release wheel requires generated/release/static/index.html; "
            "build the frontend through the release owner before building the wheel"
        )
    return {str(static_root): RELEASE_STATIC_TARGET}


def release_catalog_inclusion(*, version: str) -> dict[str, str]:
    """Verify explicit source delivery without importing the product in PEP 517."""

    if version != "standard":
        return {}
    archive_text = os.environ.get("OH_GOON_RELEASE_CATALOG_ARCHIVE", "")
    expected = os.environ.get("OH_GOON_RELEASE_CATALOG_ARCHIVE_SHA256", "")
    archive_path = Path(archive_text)
    if not archive_text or not expected or archive_path.is_symlink() or not archive_path.is_file():
        raise RuntimeError(
            "standard release wheel requires admitted template catalog source; "
            "use the existing release wheel build owner"
        )
    digest = hashlib.sha256()
    with archive_path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    if digest.hexdigest() != expected:
        raise RuntimeError("release template catalog delivery differs from admitted source")
    destination = archive_path.parent / "hatch-catalogs"
    destination.mkdir()
    with tarfile.open(archive_path, mode="r:") as archive:
        for member in archive:
            if not member.isfile() and not member.isdir():
                raise RuntimeError("release template catalog contains non-regular source")
        archive.extractall(destination, filter="data")
    if {path.name for path in destination.iterdir()} != {"goons", "plans", "examples"}:
        raise RuntimeError("release template catalog must contain goons, plans and examples")
    return {
        str(destination / "goons"): "oh_goon/templates/goons",
        str(destination / "plans"): "oh_goon/templates/plans",
        str(destination / "examples"): "oh_goon/examples",
    }


class CustomBuildHook(BuildHookInterface):
    """Attach built UI and verified captured catalogs to a standard release wheel."""

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        inclusion = release_static_inclusion(project_root=Path(self.root), version=version)
        inclusion.update(release_catalog_inclusion(version=version))
        if inclusion:
            build_data.setdefault("force_include", {}).update(inclusion)
