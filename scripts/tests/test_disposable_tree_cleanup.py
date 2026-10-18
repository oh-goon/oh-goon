from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "scripts/remove_disposable_tree.sh"


def _run(target: Path | str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("sh", str(HELPER), str(target)),
        check=False,
        capture_output=True,
        text=True,
    )


def test_remove_disposable_tree_rejects_dangerous_roots() -> None:
    for target in ("/", ".", ".."):
        result = _run(target)
        assert result.returncode == 2
        assert "refuses dangerous target" in result.stderr


def test_remove_disposable_tree_removes_read_only_tree_without_following_nested_symlink(
    tmp_path: Path,
) -> None:
    if os.name == "nt":
        pytest.skip("permission regression is POSIX-specific")
    external = tmp_path / "external"
    external.mkdir()
    external_file = external / "keep"
    external_file.write_text("external", encoding="utf-8")
    external_modes = (stat.S_IMODE(external.stat().st_mode), stat.S_IMODE(external_file.stat().st_mode))

    target = tmp_path / "disposable"
    nested = target / "a/b"
    nested.mkdir(parents=True)
    (nested / "payload").write_text("payload", encoding="utf-8")
    (nested / "escape").symlink_to(external, target_is_directory=True)
    for directory in (nested, nested.parent, target):
        directory.chmod(0o555)
    (nested / "payload").chmod(0o444)

    result = _run(target)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not target.exists()
    assert external_file.read_text(encoding="utf-8") == "external"
    assert (stat.S_IMODE(external.stat().st_mode), stat.S_IMODE(external_file.stat().st_mode)) == external_modes


def test_remove_disposable_tree_unlinks_symlink_root_without_touching_target(tmp_path: Path) -> None:
    external = tmp_path / "external"
    external.mkdir()
    external_file = external / "keep"
    external_file.write_text("external", encoding="utf-8")
    link = tmp_path / "disposable-link"
    link.symlink_to(external, target_is_directory=True)

    result = _run(link)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not link.exists() and not link.is_symlink()
    assert external_file.read_text(encoding="utf-8") == "external"
