"""P1P2B2B1: reject WSL EROFS fixture root shortcut before real FD mount."""

from __future__ import annotations

from pathlib import Path

import pytest

from oh_goon.repository_tooling.architecture.repository_layout import ROOT
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


@pytest.mark.parametrize(
    "old,new",
    (
        ("capture_runtime_root(", "capture_not_canonical_root("),
        ("verify_runtime_root(runtime)", "pass # identity verification removed"),
        ("request.attempt.runtime_root = runtime", "request.attempt.runtime_root.root = Path('/')"),
    ),
)
def test_x0_rejects_canonical_root_bypass(tmp_path: Path, old: str, new: str):
    src = ROOT / "backend/tests/native/test_p1b27b2b3b2b3a_effectful_mount.py"
    out = tmp_path / "backend/tests/native/test_p1b27b2b3b2b3a_effectful_mount.py"
    out.parent.mkdir(parents=True)
    content = src.read_text()
    assert old in content
    out.write_text(content.replace(old, new, 1))
    from oh_goon.repository_tooling.architecture.x0_single_execution_contract import (
        _check_p1p2b2b2_canonical_runtime_root,
    )

    assert _check_p1p2b2b2_canonical_runtime_root(tmp_path)
