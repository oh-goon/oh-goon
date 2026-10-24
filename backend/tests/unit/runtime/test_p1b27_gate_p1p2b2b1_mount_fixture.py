"""P1P2B2B1: reject WSL EROFS fixture root shortcut before real FD mount."""

from __future__ import annotations

from pathlib import Path

from tests.native.test_p1b27b2b3b2b3a_effectful_mount import (
    _prepared_effectful_runtime_root,
)


def test_native_runtime_scaffold_precreates_read_only_bind_targets(tmp_path: Path):
    binding, tools = _prepared_effectful_runtime_root(tmp_path / "runtime")
    runtime = binding.root
    assert {tool.name for tool in tools} == {"sh", "cat", "stat", "awk", "sleep"}
    for name in ("workspace", "home/fleet", "tmp", "etc", "proc", "dev"):
        assert (runtime / name).is_dir(), name
    for name in ("etc/passwd", "toolchain/bin/stat", "bin/sh"):
        assert (runtime / name).is_file(), name
    assert runtime != Path("/")
