from __future__ import annotations

from pathlib import Path

from oh_goon.repository_tooling.shell_syntax_check import declared_shell, shell_sources


def test_declared_shell_recognizes_supported_repository_shebangs(
    tmp_path: Path,
) -> None:
    bash = tmp_path / "one.sh"
    bash.write_text("#!/usr/bin/env bash\necho ok\n")
    sh = tmp_path / "two.sh"
    sh.write_text("#!/bin/sh\necho ok\n")
    other = tmp_path / "three.py"
    other.write_text("#!/usr/bin/env python3\n")
    assert declared_shell(bash) == "bash"
    assert declared_shell(sh) == "sh"
    assert declared_shell(other) is None


def test_repository_shell_source_discovery_uses_canonical_source_set() -> None:
    root = Path(__file__).resolve().parents[2]
    rows = shell_sources(root)
    assert rows
    assert {shell for shell, _path in rows} <= {"bash", "sh"}
    assert all(path.is_file() for _shell, path in rows)
