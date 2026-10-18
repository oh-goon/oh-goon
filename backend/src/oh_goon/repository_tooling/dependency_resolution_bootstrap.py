"""Oh-Goon-owned publication policy for the committed native uv lock.

Native uv owns dependency resolution, freshness checks, lock authoring, retries,
registry access, caching, and environment materialization. This module does not
invoke or emulate uv. It checks only repository-specific publication properties
that uv does not own.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

if __package__:
    from ._dependency_lock_portability import backend_lock_portability_issues
    from ._dependency_resolution_primitives import DependencyResolutionError
else:  # stdlib-only source execution before the project environment exists
    from _dependency_lock_portability import backend_lock_portability_issues
    from _dependency_resolution_primitives import DependencyResolutionError

ROOT = Path(__file__).resolve().parents[4]
BACKEND = ROOT / "backend"
LOCK = BACKEND / "uv.lock"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_regular(path: Path, label: str) -> None:
    if path.is_symlink() or not path.is_file():
        raise DependencyResolutionError(
            f"{label} must be a regular non-symlink file: {path.relative_to(ROOT)}"
        )


def command_public_lock_check() -> None:
    _require_regular(LOCK, "committed native dependency lock")
    portability = backend_lock_portability_issues(LOCK)
    if portability:
        rendered = "\n".join(f"- {issue}" for issue in portability)
        raise DependencyResolutionError(
            "DEPENDENCY LOCK PORTABILITY FAILED\n"
            "backend/uv.lock contains source identity that is not publishable as Oh-Goon public dependency authority.\n"
            f"{rendered}\n"
            "Recovery: author backend/uv.lock with native `uv lock --project backend` using public dependency source identity."
        )
    print(
        "DEPENDENCY LOCK PUBLICATION POLICY PASSED\n"
        f"uv.lock sha256: {_sha256(LOCK)}"
    )


def command_status() -> None:
    _require_regular(LOCK, "committed native dependency lock")
    print(
        "Dependency intent: backend/pyproject.toml\n"
        "Resolution authority: backend/uv.lock\n"
        "Resolution/materialization semantics: native uv\n"
        f"uv.lock sha256: {_sha256(LOCK)}"
    )


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="oh-goon-dependency-policy")
    result.add_argument("command", choices=("public-lock-check", "status"))
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        {"public-lock-check": command_public_lock_check, "status": command_status}[args.command]()
        return 0
    except DependencyResolutionError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
