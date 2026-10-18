from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.integrations.content_snapshot import SnapshotIdentity


@dataclass(frozen=True, slots=True)
class StrongTreeProof:
    """Process-local authority that one exact tree identity was strongly proven.

    The metadata fingerprint is only a mutation guard. It never substitutes for the
    strong identity proof that created this object. Consumers may reuse the proof
    only for the same resolved path and identity while a fresh metadata walk matches.
    """

    source_path: Path
    identity: SnapshotIdentity
    metadata_fingerprint: str

    def proves_current(self, path: Path) -> bool:
        resolved = path.resolve()
        return (
            resolved == self.source_path
            and snapshot_metadata_fingerprint(resolved) == self.metadata_fingerprint
        )


def strong_tree_proof(
    path: Path,
    identity: SnapshotIdentity,
    *,
    metadata_before: str,
    metadata_after: str,
) -> StrongTreeProof:
    """Create proof authority only when metadata stayed stable across strong hashing."""

    if metadata_before != metadata_after:
        raise FleetPlanInvalid("Strong tree proof source changed during verification")
    return StrongTreeProof(path.resolve(), identity, metadata_after)


def snapshot_metadata_fingerprint(path: Path) -> str:
    """Hash mutation-relevant stat metadata without reading regular-file contents."""

    resolved = path.resolve()
    if resolved.is_symlink() or not resolved.is_dir():
        raise FleetPlanInvalid(f"Snapshot proof source must be a directory: {path}")
    digest = hashlib.sha256()
    _fingerprint_entry(digest, resolved, relative=".")
    for current_root, directory_names, file_names in os.walk(
        resolved, topdown=True, followlinks=False
    ):
        directory_names.sort()
        file_names.sort()
        root = Path(current_root)
        for name in (*directory_names, *file_names):
            entry = root / name
            _fingerprint_entry(digest, entry, relative=entry.relative_to(resolved).as_posix())
    return "sha256:" + digest.hexdigest()


def _fingerprint_entry(digest, path: Path, *, relative: str) -> None:
    observed = path.lstat()
    mode = observed.st_mode
    if stat.S_ISLNK(mode):
        kind = "symlink"
        target = os.readlink(path)
    elif stat.S_ISDIR(mode):
        kind = "directory"
        target = ""
    elif stat.S_ISREG(mode):
        kind = "file"
        target = ""
    else:
        raise FleetPlanInvalid(f"Unsupported snapshot proof entry: {path}")
    for field in (
        relative,
        kind,
        str(observed.st_dev),
        str(observed.st_ino),
        str(stat.S_IMODE(mode)),
        str(observed.st_size),
        str(observed.st_mtime_ns),
        str(observed.st_ctime_ns),
        target,
    ):
        encoded = field.encode("utf-8", errors="surrogateescape")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
