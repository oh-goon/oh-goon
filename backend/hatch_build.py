"""Own the release-only UI payload at the Python package build boundary.

The repository source and editable developer environment do not contain a built
frontend.  Only a standard release wheel is allowed to consume
``generated/release/static``.  Keeping that mapping here means native editable
materialization never depends on a generated release directory, while release
wheel construction fails closed if its already-built UI payload is missing.
"""
from __future__ import annotations

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


class CustomBuildHook(BuildHookInterface):
    """Attach generated UI bytes only to a standard release wheel."""

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        inclusion = release_static_inclusion(project_root=Path(self.root), version=version)
        if inclusion:
            build_data.setdefault("force_include", {}).update(inclusion)
