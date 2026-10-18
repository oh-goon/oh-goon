from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from oh_goon.core.source_transport_metadata import is_disposable_source_transport_artifact_name
from oh_goon.core.source_tree_policy import canonical_lock_layout_issues, is_transient_source_path


@dataclass(frozen=True, slots=True)
class SourceLayoutIssue:
    path: Path
    reason: str


def source_transport_artifact_issues(root: Path) -> tuple[SourceLayoutIssue, ...]:
    """Return disposable transport artifacts that contaminate packageable source."""

    root = root.resolve()
    issues: list[SourceLayoutIssue] = []
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
            if is_disposable_source_transport_artifact_name(name):
                issues.append(
                    SourceLayoutIssue(
                        current / name,
                        "disposable source transport artifact must be removed before "
                        "source admission; run oh-goon-source-layout transport-clean explicitly",
                    )
                )
                continue
            kept.append(name)
        dirnames[:] = kept
        for name in filenames:
            if not is_disposable_source_transport_artifact_name(name):
                continue
            issues.append(
                SourceLayoutIssue(
                    current / name,
                    "disposable source transport artifact must be removed before "
                    "source admission; run oh-goon-source-layout transport-clean explicitly",
                )
            )
    return tuple(sorted(issues, key=lambda issue: issue.path.relative_to(root).as_posix()))


def canonical_source_layout_issues(
    root: Path,
    *,
    check_tracked_generated: bool = True,
) -> tuple[SourceLayoutIssue, ...]:
    """Validate immutable source-layout invariants for one explicit source root."""

    root = root.resolve()
    issues: list[SourceLayoutIssue] = [
        SourceLayoutIssue(path=path, reason=reason)
        for path, reason in canonical_lock_layout_issues(root)
    ]
    issues.extend(source_transport_artifact_issues(root))

    if check_tracked_generated and (root / ".git").exists():
        try:
            result = subprocess.run(
                ("git", "ls-files", "-z"),
                cwd=root,
                check=False,
                capture_output=True,
            )
        except OSError as exc:
            issues.append(
                SourceLayoutIssue(root / ".git", f"cannot inspect tracked source paths: {exc}")
            )
        else:
            if result.returncode != 0:
                issues.append(
                    SourceLayoutIssue(
                        root / ".git",
                        "cannot inspect tracked source paths with git ls-files",
                    )
                )
            else:
                for raw in result.stdout.split(b"\0"):
                    if not raw:
                        continue
                    try:
                        relative = PurePosixPath(raw.decode("utf-8"))
                    except UnicodeDecodeError:
                        issues.append(SourceLayoutIssue(root, "tracked source path is not UTF-8"))
                        continue
                    if is_transient_source_path(relative):
                        issues.append(
                            SourceLayoutIssue(
                                root / Path(relative),
                                "generated/operational path must never be tracked as source",
                            )
                        )
    return tuple(issues)


def summarize_source_layout_issues(
    root: Path,
    issues: tuple[SourceLayoutIssue, ...] | list[SourceLayoutIssue],
    *,
    debug: bool,
) -> tuple[SourceLayoutIssue, ...]:
    """Bound high-volume transport diagnostics without changing source authority."""

    resolved_root = root.resolve()
    transport = [
        issue for issue in issues
        if is_disposable_source_transport_artifact_name(issue.path.name)
    ]
    other = [
        issue for issue in issues
        if not is_disposable_source_transport_artifact_name(issue.path.name)
    ]
    if not transport or debug:
        return tuple((*other, *transport))
    count = len(transport)
    aggregate = SourceLayoutIssue(
        resolved_root,
        f"{count} disposable source transport "
        f"artifact{'s' if count != 1 else ''} contaminate source; "
        "run oh-goon-source-layout transport-clean explicitly "
        "(rerun with --debug for exact paths)",
    )
    return tuple((*other, aggregate))


def render_source_layout_issues(
    root: Path,
    issues: tuple[SourceLayoutIssue, ...] | list[SourceLayoutIssue],
    *,
    debug: bool,
) -> tuple[str, ...]:
    """Render source-layout diagnostics without flooding normal operator output."""

    resolved_root = root.resolve()
    summarized = summarize_source_layout_issues(root, issues, debug=debug)
    lines: list[str] = []
    for issue in summarized:
        if issue.path.resolve() == resolved_root:
            lines.append(issue.reason)
        else:
            lines.append(f"{issue.path.relative_to(resolved_root)}: {issue.reason}")
    return tuple(lines)


def assert_canonical_source_layout(root: Path, *, debug: bool = False) -> None:
    """Fail closed when one explicit source root is not admissible."""

    issues = canonical_source_layout_issues(root)
    if not issues:
        return
    rendered = "; ".join(render_source_layout_issues(root, list(issues), debug=debug))
    raise ValueError(f"repository source layout is invalid: {rendered}")
