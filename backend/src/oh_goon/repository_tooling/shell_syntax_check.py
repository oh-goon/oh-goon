from __future__ import annotations

import subprocess
from pathlib import Path

from oh_goon.repository_tooling.release.package_slim import canonical_source_files

ROOT = Path(__file__).resolve().parents[4]


def declared_shell(path: Path) -> str | None:
    try:
        first = path.open("rb").readline(256).decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    line = first.strip()
    if line in {"#!/bin/sh", "#!/usr/bin/env sh"}:
        return "sh"
    if line in {"#!/bin/bash", "#!/usr/bin/env bash"}:
        return "bash"
    return None


def shell_sources(root: Path = ROOT) -> tuple[tuple[str, Path], ...]:
    rows: list[tuple[str, Path]] = []
    for path in canonical_source_files(root):
        shell = declared_shell(path)
        if shell is not None:
            rows.append((shell, path))
    return tuple(rows)


def verify_shell_syntax(root: Path = ROOT) -> int:
    failures: list[str] = []
    for shell, path in shell_sources(root):
        result = subprocess.run(
            [shell, "-n", str(path)],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            relative = path.relative_to(root).as_posix()
            detail = (result.stderr or result.stdout).strip()
            failures.append(f"{relative}: {detail or f'exit {result.returncode}'}")
    if failures:
        print("OH-GOON SHELL SYNTAX FAILED")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print(f"OH-GOON SHELL SYNTAX PASS ({len(shell_sources(root))} files)")
    return 0


def main() -> int:
    return verify_shell_syntax(ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
