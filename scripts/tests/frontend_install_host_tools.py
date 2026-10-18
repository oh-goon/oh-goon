from __future__ import annotations

import os
import shutil
from pathlib import Path

_HOST_TOOL_NAMES = (
    "cat",
    "chmod",
    "cp",
    "cut",
    "dirname",
    "find",
    "ln",
    "mkdir",
    "mv",
    "readlink",
    "rm",
    "sha256sum",
    "sh",
    "sleep",
)


def _host_tool(name: str) -> Path:
    resolved = shutil.which(name)
    if resolved is not None:
        candidate = Path(resolved)
        if candidate.is_file():
            return candidate.resolve()
    raise AssertionError(
        f"required frontend-install test runtime tool is unavailable in admitted PATH: {name}"
    )


def frontend_install_host_env(tmp_path: Path) -> tuple[dict[str, str], Path]:
    """Provide bounded host-frontdoor tools without inheriting ambient PATH."""

    tool_dir = tmp_path / "frontend-install-host-tools"
    tool_dir.mkdir()
    for name in _HOST_TOOL_NAMES:
        target = _host_tool(name)
        wrapper = tool_dir / name
        wrapper.write_text(f'#!/bin/sh\nexec "{target}" "$@"\n', encoding="utf-8")
        wrapper.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = str(tool_dir)
    return env, tool_dir


def frontend_install_host_tool_names() -> tuple[str, ...]:
    return _HOST_TOOL_NAMES
