from __future__ import annotations

import os
import shutil
from pathlib import Path

from oh_goon.core.errors import FleetPlanInvalid


def required_certification_tool(name: str) -> Path:
    resolved = shutil.which(name)
    if not resolved:
        raise FleetPlanInvalid(
            f"Strict release/CI execution requires host tool {name!r}; install it before retrying the strict operation"
        )
    path = Path(resolved).resolve()
    if not path.is_file():
        raise FleetPlanInvalid(f"Strict release/CI execution tool is not a regular file: {path}")
    return path


def certification_tool_search_paths(environment: dict[str, str]) -> tuple[Path, ...]:
    paths: list[Path] = []
    for raw in environment.get("PATH", "").split(os.pathsep):
        if not raw:
            continue
        path = Path(raw)
        if not path.is_absolute() or not path.is_dir():
            continue
        resolved = path.resolve()
        if resolved == Path(resolved.anchor) or resolved in paths:
            continue
        paths.append(resolved)
    return tuple(paths)
