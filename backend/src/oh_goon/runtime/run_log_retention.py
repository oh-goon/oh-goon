from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

MAX_RETAINED_RUN_LOG_FILES = 20
MAX_RETAINED_RUN_LOG_BYTES = 100 * 1024 * 1024
_COMPLETED_RUN_LOG_NAME = re.compile(r"^\d{8}T\d{6}Z-\d+\.log$")


@dataclass(frozen=True, slots=True)
class RunLogPruneResult:
    removed_files: tuple[str, ...]
    retained_files: tuple[str, ...]
    retained_bytes: int


def prune_run_log_directory(path: Path) -> RunLogPruneResult:
    """Bound completed immutable run logs; never delete the latest presentation alias."""

    if not path.exists():
        return RunLogPruneResult((), (), 0)
    if path.is_symlink() or not path.is_dir():
        raise RuntimeError(f"run-log retention requires a real directory: {path}")
    candidates = [
        item
        for item in path.iterdir()
        if item.is_file() and not item.is_symlink() and _COMPLETED_RUN_LOG_NAME.fullmatch(item.name)
    ]
    candidates.sort(key=lambda item: (item.stat().st_mtime_ns, item.name), reverse=True)
    retained: list[Path] = []
    removed: list[str] = []
    retained_bytes = 0
    for index, item in enumerate(candidates):
        size = item.stat().st_size
        keep = index == 0 or (
            len(retained) < MAX_RETAINED_RUN_LOG_FILES
            and retained_bytes + size <= MAX_RETAINED_RUN_LOG_BYTES
        )
        if keep:
            retained.append(item)
            retained_bytes += size
            continue
        item.unlink()
        removed.append(item.name)
    return RunLogPruneResult(
        tuple(sorted(removed)), tuple(item.name for item in retained), retained_bytes
    )


def prune_run_log_tree(root: Path) -> tuple[RunLogPruneResult, ...]:
    """Apply the one canonical run-log policy to every label directory below root."""

    if not root.exists():
        return ()
    if root.is_symlink() or not root.is_dir():
        raise RuntimeError(f"run-log root must be a real directory: {root}")
    results: list[RunLogPruneResult] = []
    for directory in sorted(path for path in root.rglob("*") if path.is_dir()):
        if any(
            _COMPLETED_RUN_LOG_NAME.fullmatch(item.name)
            for item in directory.iterdir()
            if item.is_file()
        ):
            results.append(prune_run_log_directory(directory))
    return tuple(results)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: python -m oh_goon.runtime.run_log_retention <log-directory>", file=sys.stderr)
        return 2
    prune_run_log_directory(Path(args[0]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
