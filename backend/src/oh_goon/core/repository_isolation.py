from __future__ import annotations

import os
import stat
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.source_tree_policy import is_transient_source_path



@dataclass(slots=True)
class _RepositoryRootTrieNode:
    children: dict[str, "_RepositoryRootTrieNode"] = field(default_factory=dict)
    terminal_min_index: int | None = None
    subtree_min_index: int | None = None


def require_disjoint_repository_roots(roots: Sequence[Path]) -> tuple[Path, ...]:
    """Resolve one selected repository set and prove it is pairwise disjoint.

    The proof keeps exact deterministic pair-order semantics without enumerating every
    pair. Path ancestry is indexed in a prefix trie and filesystem directory identity
    is captured once per root. If multiple conflicts exist, the lexicographically
    earliest ``(left_index, right_index)`` pair is reported, matching the historical
    nested-loop proof.
    """

    resolved = tuple(root.resolve() for root in roots)
    identities = tuple(_repository_directory_identity(root) for root in resolved)
    trie = _RepositoryRootTrieNode()
    identity_first_index: dict[tuple[int, int], int] = {}
    first_conflict: tuple[int, int, str] | None = None

    for index, (root, identity) in enumerate(zip(resolved, identities, strict=True)):
        overlap_index = _first_overlapping_prior_index(trie, root)
        alias_index = identity_first_index.get(identity) if identity is not None else None
        candidate = _selected_root_conflict(index, overlap_index, alias_index)
        if candidate is not None and (first_conflict is None or candidate[:2] < first_conflict[:2]):
            first_conflict = candidate
        _insert_repository_root(trie, root, index)
        if identity is not None:
            identity_first_index.setdefault(identity, index)

    if first_conflict is not None:
        left_index, right_index, kind = first_conflict
        left = resolved[left_index]
        right = resolved[right_index]
        if kind == "overlap":
            raise FleetPlanInvalid(
                f"Strict CI/CD repository roots must not overlap: {left} and {right}"
            )
        raise FleetPlanInvalid(
            "Strict CI/CD repository roots must not alias the same filesystem directory: "
            f"{left} and {right}"
        )
    return resolved


def _selected_root_conflict(
    right_index: int, overlap_index: int | None, alias_index: int | None
) -> tuple[int, int, str] | None:
    if overlap_index is not None and (alias_index is None or overlap_index <= alias_index):
        return overlap_index, right_index, "overlap"
    if alias_index is not None:
        return alias_index, right_index, "alias"
    return None


def _first_overlapping_prior_index(trie: _RepositoryRootTrieNode, root: Path) -> int | None:
    node = trie
    ancestor_min: int | None = None
    for part in root.parts:
        child = node.children.get(part)
        if child is None:
            return ancestor_min
        node = child
        if node.terminal_min_index is not None:
            ancestor_min = (
                node.terminal_min_index
                if ancestor_min is None
                else min(ancestor_min, node.terminal_min_index)
            )
    descendant_min = node.subtree_min_index
    if ancestor_min is None:
        return descendant_min
    if descendant_min is None:
        return ancestor_min
    return min(ancestor_min, descendant_min)


def _insert_repository_root(trie: _RepositoryRootTrieNode, root: Path, index: int) -> None:
    node = trie
    if node.subtree_min_index is None or index < node.subtree_min_index:
        node.subtree_min_index = index
    for part in root.parts:
        node = node.children.setdefault(part, _RepositoryRootTrieNode())
        if node.subtree_min_index is None or index < node.subtree_min_index:
            node.subtree_min_index = index
    if node.terminal_min_index is None or index < node.terminal_min_index:
        node.terminal_min_index = index


def _repository_directory_identity(root: Path) -> tuple[int, int] | None:
    """Return same-directory identity, preserving ``samefile`` OSError semantics."""

    try:
        metadata = root.stat()
    except OSError:
        return None
    return metadata.st_dev, metadata.st_ino


def require_isolated_repository_tree(
    root: Path, *, other_repository_roots: Iterable[Path] = ()
) -> None:
    """Prove a strict repository tree cannot expose bytes owned outside that tree."""

    root = root.resolve()
    _require_disjoint_repository_roots(root, other_repository_roots)
    _require_no_nested_mounts(root, ignore_transient=True)
    _require_single_repository_filesystem_tree(root, reject_nested_git=True, ignore_transient=True)


def require_owned_filesystem_tree(root: Path) -> None:
    """Prove a Fleet-private tree has no live filesystem edge outside itself."""

    root = root.resolve()
    _require_no_nested_mounts(root, ignore_transient=False)
    _require_single_repository_filesystem_tree(root, reject_nested_git=False, ignore_transient=False)


def _require_disjoint_repository_roots(root: Path, other_repository_roots: Iterable[Path]) -> None:
    root_identity = _repository_directory_identity(root)
    for other_value in other_repository_roots:
        other = other_value.resolve()
        _require_disjoint_repository_pair(
            root,
            other,
            root_identity=root_identity,
            other_identity=_repository_directory_identity(other),
        )


def _require_disjoint_repository_pair(
    root: Path,
    other: Path,
    *,
    root_identity: tuple[int, int] | None,
    other_identity: tuple[int, int] | None,
) -> None:
    if other == root or root.is_relative_to(other) or other.is_relative_to(root):
        raise FleetPlanInvalid(
            f"Strict CI/CD repository roots must not overlap: {root} and {other}"
        )
    if root_identity is not None and root_identity == other_identity:
        raise FleetPlanInvalid(
            "Strict CI/CD repository roots must not alias the same filesystem directory: "
            f"{root} and {other}"
        )


def _require_no_nested_mounts(root: Path, *, ignore_transient: bool) -> None:
    """Reject mount points below a strict repository root, including bind mounts."""

    if sys.platform != "linux":
        raise FleetPlanInvalid(
            "Strict CI/CD repository mount-topology proof requires Linux /proc/self/mountinfo"
        )
    for mount_point in _visible_mount_points():
        if mount_point != root and mount_point.is_relative_to(root):
            relative = mount_point.relative_to(root)
            if ignore_transient and is_transient_source_path(relative):
                continue
            raise FleetPlanInvalid(
                "Strict CI/CD repository contains a nested mount point; external bytes "
                f"are not repository-owned: {relative}"
            )


def _visible_mount_points() -> tuple[Path, ...]:
    mountinfo = Path("/proc/self/mountinfo")
    try:
        lines = mountinfo.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise FleetPlanInvalid(
            "Strict CI/CD repository mount-topology proof cannot read /proc/self/mountinfo"
        ) from exc
    result: list[Path] = []
    for line in lines:
        fields = line.split(" - ", 1)[0].split()
        if len(fields) < 5:
            raise FleetPlanInvalid(
                "Strict CI/CD repository mount-topology proof encountered malformed mountinfo"
            )
        result.append(Path(_decode_mountinfo_path(fields[4])).resolve())
    return tuple(result)


def _decode_mountinfo_path(value: str) -> str:
    return (
        value.replace(r"\040", " ")
        .replace(r"\011", "\t")
        .replace(r"\012", "\n")
        .replace(r"\134", "\\")
    )


def _require_single_repository_filesystem_tree(
    root: Path, *, reject_nested_git: bool, ignore_transient: bool
) -> None:
    if not root.is_dir():
        raise FleetPlanInvalid(f"Strict CI/CD repository root is unavailable: {root}")
    try:
        root_device = root.stat().st_dev
    except OSError as exc:
        raise FleetPlanInvalid(
            f"Cannot inspect strict CI/CD repository root: {root}: {exc}"
        ) from exc

    for current, directories, files in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        relative_current = current_path.relative_to(root)
        if reject_nested_git and current_path != root and ".git" in {*directories, *files}:
            relative = current_path.relative_to(root)
            raise FleetPlanInvalid(
                "Strict CI/CD repository contains a nested Git repository/submodule; "
                f"declare it as separate dataflow instead of inheriting its bytes: {relative}"
            )
        if ignore_transient:
            directories[:] = [
                name
                for name in directories
                if not is_transient_source_path(relative_current / name)
            ]
            files = [
                name
                for name in files
                if not is_transient_source_path(relative_current / name)
            ]

        for name in (*directories, *files):
            path = current_path / name
            relative = path.relative_to(root)
            try:
                metadata = path.lstat()
            except OSError as exc:
                raise FleetPlanInvalid(
                    f"Cannot inspect strict CI/CD repository path {relative}: {exc}"
                ) from exc
            mode = metadata.st_mode
            if stat.S_ISLNK(mode):
                try:
                    target = path.resolve(strict=False)
                except OSError as exc:
                    raise FleetPlanInvalid(
                        f"Cannot resolve strict CI/CD repository symlink {relative}: {exc}"
                    ) from exc
                if target != root and not target.is_relative_to(root):
                    raise FleetPlanInvalid(
                        f"Strict CI/CD repository contains escaping symlink: {relative}"
                    )
                continue
            if metadata.st_dev != root_device:
                raise FleetPlanInvalid(
                    "Strict CI/CD repository crosses a filesystem/mount boundary at "
                    f"{relative}; external mount bytes are not repository-owned"
                )
            if stat.S_ISREG(mode):
                if metadata.st_nlink != 1:
                    raise FleetPlanInvalid(
                        "Strict CI/CD repository contains a multiply-linked regular file; "
                        f"hard-link ownership is not accepted: {relative}"
                    )
                continue
            if stat.S_ISDIR(mode):
                continue
            raise FleetPlanInvalid(
                f"Strict CI/CD repository contains unsupported filesystem entry: {relative}"
            )
