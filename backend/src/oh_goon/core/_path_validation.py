from __future__ import annotations

from pathlib import Path
from typing import Literal

from oh_goon.core.errors import FleetPlanInvalid

PathKind = Literal["any", "file", "directory"]


def require_path_kind(
    path: Path,
    *,
    label: str,
    kind: PathKind,
    require_exists: bool,
) -> None:
    """Validate filesystem kind/existence without inventing path authority."""

    if require_exists and not path.exists():
        raise FleetPlanInvalid(f"{label} does not exist: {path}")
    if kind == "file" and path.exists() and not path.is_file():
        raise FleetPlanInvalid(f"{label} is not a file: {path}")
    if kind == "directory" and path.exists() and not path.is_dir():
        raise FleetPlanInvalid(f"{label} is not a directory: {path}")
