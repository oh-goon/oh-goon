from __future__ import annotations

import os
import subprocess
from pathlib import Path

from oh_goon.core.path_authority import require_absolute_host_path


def find_uv_system_python(executable: Path) -> Path | None:
    """Return the exact system Python selected by one admitted uv executable.

    Keep discovery independent of operator PATH/virtualenv state so certification
    supply preparation and strict Fleet runtime capture bind the same base runtime.
    """

    completed = subprocess.run(
        (str(executable), "python", "find", "--system"),
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
        env={
            "PATH": os.defpath,
            "LC_ALL": "C",
            "LANG": "C",
            "UV_NO_MANAGED_PYTHON": "1",
        },
    )
    if completed.returncode != 0:
        return None
    first = next((line.strip() for line in completed.stdout.splitlines() if line.strip()), "")
    if not first:
        return None
    path = require_absolute_host_path(first, label="uv system Python")
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        return None
    return resolved if resolved.is_file() and not resolved.is_symlink() else None
