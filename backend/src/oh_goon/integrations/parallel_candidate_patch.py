"""Native Git patch representation and round-trip proof in private scratch."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.agent_source import MAX_REPOSITORY_BYTES, MAX_SOURCE_ENTRIES
from oh_goon.engine.git import GitService
from oh_goon.integrations.content_snapshot import SnapshotIdentity
from oh_goon.integrations.source_snapshot import snapshot_source_directory, source_snapshot_identity

MAX_PARALLEL_PATCH_BYTES = 16 * 1024 * 1024
_MAX_PATH_INVENTORY_BYTES = 4 * 1024 * 1024
_NATIVE_OBJECT_ID = re.compile(r"[a-f0-9]{40}|[a-f0-9]{64}")


@dataclass(frozen=True, slots=True)
class NativeCandidatePatch:
    path: Path
    changed_paths: tuple[str, ...]
    tree: str


def _git(git: GitService, cwd: Path, *arguments: str) -> str:
    result = git.run(*arguments, cwd=cwd, max_capture_bytes_per_stream=64 * 1024)
    if result.returncode != 0 or result.stdout_truncated:
        raise FleetPlanInvalid("Native Git could not represent or replay the candidate patch")
    return result.stdout


def _tree(git: GitService, cwd: Path, *arguments: str) -> str:
    identity = _git(git, cwd, *arguments)
    if _NATIVE_OBJECT_ID.fullmatch(identity) is None:
        raise FleetPlanInvalid("Native Git returned an unsupported tree identity")
    return identity


def _paths(inventory: Path) -> tuple[str, ...]:
    if inventory.stat().st_size > _MAX_PATH_INVENTORY_BYTES:
        raise FleetPlanInvalid("Native candidate patch path inventory exceeds its bound")
    try:
        paths = inventory.read_bytes().decode("utf-8").split("\x00")
    except UnicodeDecodeError as error:
        raise FleetPlanInvalid(
            "Native candidate patch contains unsupported path encoding"
        ) from error
    if paths[-1] != "" or len(set(paths)) != len(paths) or len(paths) < 2:
        raise FleetPlanInvalid("Native candidate patch has no complete changed path inventory")
    return tuple(sorted(paths[:-1]))


def prepare_native_candidate_patch(
    git: GitService,
    *,
    original: Path,
    original_revision: str,
    base_snapshot: Path,
    base_identity: SnapshotIdentity,
    candidate_identity: SnapshotIdentity,
    scratch: Path,
) -> NativeCandidatePatch:
    """Use native Git and a reconstruction to account for the exact changed sources."""
    if _NATIVE_OBJECT_ID.fullmatch(original_revision) is None:
        raise FleetPlanInvalid("Candidate base has no admitted native revision")
    git.require_no_ignored_files(original)
    status = git.observe(original, full_untracked_inventory=True)
    if status.head_sha != original_revision:
        raise FleetPlanInvalid("Candidate Git revision changed before export")
    original_base = _tree(git, original, "rev-parse", original_revision + "^{tree}")
    replay = scratch / "replay"
    candidate = scratch / "candidate"
    for source, destination, expected in (
        (base_snapshot, replay, base_identity),
        (original, candidate, candidate_identity),
    ):
        captured = snapshot_source_directory(
            source,
            destination,
            label="Verified candidate patch source",
            max_bytes=MAX_REPOSITORY_BYTES,
            max_entries=MAX_SOURCE_ENTRIES,
            reject_dotenv_state=True,
        )
        if captured != expected:
            raise FleetPlanInvalid("Candidate export source differs from its proved identity")
    object_format = "sha1" if len(original_revision) == 40 else "sha256"
    _git(git, replay, "init", "--quiet", "--template=", f"--object-format={object_format}")
    _git(git, replay, "add", "--all", "--force", "--", ".")
    if _tree(git, replay, "write-tree") != original_base:
        raise FleetPlanInvalid("Retained base cannot be represented as its exact native Git tree")
    _git(
        git,
        replay,
        f"--git-dir={replay / '.git'}",
        f"--work-tree={candidate}",
        "add",
        "--all",
        "--force",
        "--",
        ":/",
    )
    candidate_tree = _tree(git, replay, "write-tree")
    inventory = scratch / "paths"
    options = ("--no-ext-diff", "--no-textconv", "--no-renames")
    _git(
        git,
        replay,
        "diff",
        *options,
        "--name-only",
        "-z",
        f"--output={inventory}",
        original_base,
        candidate_tree,
    )
    paths = _paths(inventory)
    if paths != status.changed_files:
        raise FleetPlanInvalid("Candidate has changes outside the complete native patch inventory")
    patch = scratch / "candidate.patch"
    _git(
        git,
        replay,
        "diff",
        *options,
        "--binary",
        "--full-index",
        "--no-color",
        "--no-indent-heuristic",
        "--src-prefix=a/",
        "--dst-prefix=b/",
        f"--output={patch}",
        original_base,
        candidate_tree,
    )
    if not 0 < patch.stat().st_size <= MAX_PARALLEL_PATCH_BYTES:
        raise FleetPlanInvalid("Native candidate patch exceeds the supported export size")
    _git(git, replay, "read-tree", original_base)
    _git(git, replay, "update-index", "--refresh")
    _git(git, replay, "apply", "--index", "--whitespace=nowarn", str(patch))
    if (
        _tree(git, replay, "write-tree") != candidate_tree
        or source_snapshot_identity(replay, reject_dotenv_state=True) != candidate_identity
    ):
        raise FleetPlanInvalid("Native candidate patch does not reconstruct the verified source")
    return NativeCandidatePatch(patch, paths, candidate_tree)
