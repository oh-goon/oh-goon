"""Disposable developer-environment transport boundary."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

if __package__:
    from ._dependency_resolution_primitives import DependencyResolutionError
else:  # direct pre-environment script execution
    from _dependency_resolution_primitives import DependencyResolutionError


def discard_unusable_developer_environment(backend: Path, root: Path) -> None:
    """Discard only a local developer venv whose interpreter cannot execute."""

    environment = backend / ".venv"
    if not environment.exists():
        return
    if environment.is_symlink() or not environment.is_dir():
        raise DependencyResolutionError(
            "DEVELOPER ENVIRONMENT INVALID\n"
            "backend/.venv must be a disposable local directory, never a symlink or file."
        )
    candidates = (environment / "bin/python", environment / "bin/python3")
    interpreter = next((path for path in candidates if path.is_file()), None)
    usable = False
    if interpreter is not None and os.access(interpreter, os.X_OK):
        try:
            probe = subprocess.run(
                (str(interpreter), "--version"),
                cwd=root,
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
            usable = probe.returncode == 0
        except (OSError, subprocess.SubprocessError):
            usable = False
    if usable:
        return
    shutil.rmtree(environment)
    print(
        "Developer environment: discarded unusable/copied backend/.venv before native uv materialization"
    )
