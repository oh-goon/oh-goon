from __future__ import annotations

import os
import stat
from pathlib import Path, PurePosixPath

from oh_goon.core.source_layout_policy import (
    SourceLayoutIssue,
)
from oh_goon.core.source_layout_policy import (
    assert_canonical_source_layout as assert_canonical_source_layout,
)
from oh_goon.core.source_layout_policy import (
    canonical_source_layout_issues as canonical_source_layout_issues,
)
from oh_goon.core.source_layout_policy import (
    render_source_layout_issues as _render_source_layout_issues,
)
from oh_goon.core.source_layout_policy import (
    source_transport_artifact_issues as source_transport_artifact_issues,
)
from oh_goon.core.source_layout_policy import (
    summarize_source_layout_issues as summarize_source_layout_issues,
)
from oh_goon.core.source_transport_metadata import is_disposable_source_transport_artifact_name
from oh_goon.core.source_tree_policy import CANONICAL_BACKEND_LOCK as CANONICAL_BACKEND_LOCK
from oh_goon.core.source_tree_policy import CANONICAL_BACKEND_VENV as CANONICAL_BACKEND_VENV
from oh_goon.core.source_tree_policy import TRANSIENT_DIRECTORY_NAMES as TRANSIENT_DIRECTORY_NAMES
from oh_goon.core.source_tree_policy import TRANSIENT_FILE_NAMES as TRANSIENT_FILE_NAMES
from oh_goon.core.source_tree_policy import TRANSIENT_RELATIVE_PATHS as TRANSIENT_RELATIVE_PATHS
from oh_goon.core.source_tree_policy import (
    is_transient_source_path as is_transient_source_path,
)

ROOT = Path(__file__).resolve().parents[4]


class SourceTransportArtifactCleanupError(ValueError):
    def __init__(self, path: Path) -> None:
        self.path = path
        super().__init__("refusing non-regular source transport cleanup target")


def prepared_source_tree_issues(root: Path) -> tuple[SourceLayoutIssue, ...]:
    """Require a copied/certifiable tree to contain source only, never generated state."""

    root = root.resolve()
    issues = list(canonical_source_layout_issues(root, check_tracked_generated=False))
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        current = Path(directory)
        relative_dir = current.relative_to(root)
        for name in [*dirnames, *filenames]:
            relative = PurePosixPath(*relative_dir.parts, name)
            if relative.parts and relative.parts[0] == ".git":
                continue
            if is_transient_source_path(relative):
                issues.append(
                    SourceLayoutIssue(
                        root / Path(relative),
                        "generated/operational path leaked into prepared source tree",
                    )
                )
    return tuple(issues)


def assert_prepared_source_tree(root: Path, *, debug: bool = False) -> None:
    issues = prepared_source_tree_issues(root)
    if not issues:
        return
    rendered = "; ".join(_render_source_layout_issues(root, issues, debug=debug))
    raise ValueError(f"prepared source tree is invalid: {rendered}")


def clean_generated_venvs(root: Path = ROOT) -> tuple[Path, ...]:
    """Remove virtualenv directories only; do not clean unrelated generated source state."""

    import shutil

    root = root.resolve()
    candidates: list[Path] = []
    for directory, dirnames, _filenames in os.walk(root, topdown=True, followlinks=False):
        current = Path(directory)
        if current == root / ".git" or (root / ".git") in current.parents:
            dirnames[:] = []
            continue
        kept: list[str] = []
        for name in dirnames:
            path = current / name
            if name != ".venv":
                kept.append(name)
                continue
            if path.is_symlink():
                raise ValueError(
                    f"refusing symlink virtualenv cleanup target: {path.relative_to(root)}"
                )
            candidates.append(path)
        dirnames[:] = kept
    removed: list[Path] = []
    for path in sorted(candidates, key=lambda item: len(item.parts), reverse=True):
        if path.is_dir():
            shutil.rmtree(path)
            removed.append(path.relative_to(root))
    return tuple(sorted(removed, key=lambda item: item.as_posix()))


def clean_source_transport_artifacts(root: Path = ROOT) -> tuple[Path, ...]:
    """Remove known disposable source transport artifacts without touching other files."""

    root = root.resolve()
    removed: list[Path] = []
    for directory, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        current = Path(directory)
        relative_dir = current.relative_to(root)
        kept: list[str] = []
        for name in dirnames:
            relative = PurePosixPath(*relative_dir.parts, name)
            if relative.parts and relative.parts[0] == ".git":
                continue
            if is_transient_source_path(relative):
                continue
            kept.append(name)
        dirnames[:] = kept
        for name in filenames:
            if not is_disposable_source_transport_artifact_name(name):
                continue
            path = current / name
            mode = path.lstat().st_mode
            if not stat.S_ISREG(mode):
                raise SourceTransportArtifactCleanupError(path.relative_to(root))
            path.unlink()
            removed.append(path.relative_to(root))
    return tuple(sorted(removed, key=lambda item: item.as_posix()))


def _venv_clean_command() -> int:
    removed = clean_generated_venvs(ROOT)
    print(
        f"Removed {len(removed)} generated virtualenv director{'y' if len(removed) == 1 else 'ies'}"
    )
    for path in removed:
        print(path.as_posix())
    return 0


def _transport_clean_command(*, debug: bool = False) -> int:
    removed = clean_source_transport_artifacts(ROOT)
    print(
        f"Removed {len(removed)} disposable source transport "
        f"artifact{'s' if len(removed) != 1 else ''}"
    )
    if debug:
        for path in removed:
            print(path.as_posix())
    return 0


def _check_command(*, debug: bool = False) -> int:
    issues = canonical_source_layout_issues(ROOT)
    if not issues:
        print("Canonical source layout passed")
        return 0
    import sys

    for line in _render_source_layout_issues(ROOT, list(issues), debug=debug):
        print(line, file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="oh-goon-source-layout")
    parser.add_argument(
        "command",
        choices=("check", "identity", "venv-clean", "transport-clean"),
        nargs="?",
        default="check",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="show exact paths for high-volume source transport artifact diagnostics",
    )
    args = parser.parse_args(argv)
    try:
        if args.command == "check":
            return _check_command(debug=args.debug)
        if args.command == "identity":
            # Lazy import avoids making source_layout and package_slim mutually
            # authoritative at import time. package_slim owns canonical source
            # membership/identity and already imports source-layout policy helpers.
            from oh_goon.repository_tooling.release.package_slim import canonical_source_identity

            print(canonical_source_identity(ROOT))
            return 0
        if args.command == "transport-clean":
            return _transport_clean_command(debug=args.debug)
        return _venv_clean_command()
    except SourceTransportArtifactCleanupError as exc:
        import sys

        detail = (
            f": {exc.path.as_posix()}" if args.debug else " (rerun with --debug for exact path)"
        )
        print(f"{exc}{detail}", file=sys.stderr)
        return 2
    except (OSError, ValueError) as exc:
        import sys

        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
