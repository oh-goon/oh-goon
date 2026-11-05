"""Native repository-evidence lifetime with explicit confidentiality policy.

Oh-Goon selects the private source scope; Hashmarks interprets and enforces its
context policy. Existing repository rules remain verbatim and precede the
trusted exclusions. Canonical content/custody identity has a separate scope.
"""

from __future__ import annotations

import os
import stat
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from oh_goon.core.source_tree_policy import (
    GRADER_PRIVATE_SOURCE_ROOT,
    is_grader_private_source_path,
)

if TYPE_CHECKING:
    from hashmarks import CodeMap

_MAX_POLICY_BYTES = 1_048_576


def _file_identity(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _directory_identity(info: os.stat_result) -> tuple[int, int, int]:
    return info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode)


@dataclass(frozen=True, slots=True)
class _RepositoryRootCapture:
    selected: Path
    resolved: Path
    identity: tuple[int, int, int]

    def verify(self) -> None:
        canonical = self.selected.resolve(strict=True)
        if (
            canonical != self.resolved
            or is_grader_private_source_path(canonical)
            or _directory_identity(self.resolved.lstat()) != self.identity
        ):
            raise ValueError("Repository evidence root changed during observation")


def _capture_root(root: Path) -> _RepositoryRootCapture:
    selected = root.expanduser().absolute()
    resolved = selected.resolve(strict=True)
    if is_grader_private_source_path(selected) or is_grader_private_source_path(resolved):
        raise ValueError("Grader-private source cannot be a repository evidence root")
    observed = resolved.lstat()
    if not stat.S_ISDIR(observed.st_mode):
        raise ValueError("Repository evidence root must be a directory")
    capture = _RepositoryRootCapture(selected, resolved, _directory_identity(observed))
    capture.verify()
    return capture


@dataclass(frozen=True, slots=True)
class _RepositoryPolicyCapture:
    path: Path
    identity: tuple[int, ...] | None
    data: bytes = field(repr=False)

    def verify(self) -> None:
        try:
            current = _file_identity(self.path.lstat())
        except FileNotFoundError:
            current = None
        if current != self.identity:
            raise ValueError("Repository evidence policy changed during observation")


def _capture_policy(root: Path) -> _RepositoryPolicyCapture:
    path = root / ".hashmarks-context.toml"
    try:
        observed = path.lstat()
    except FileNotFoundError:
        return _RepositoryPolicyCapture(path, None, b"")
    if not stat.S_ISREG(observed.st_mode):
        raise ValueError("Repository evidence policy must be a regular non-symlink file")
    if not hasattr(os, "O_NOFOLLOW"):
        raise ValueError("Repository evidence policy requires native no-follow file reads")
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        opened = os.fstat(stream.fileno())
        if not stat.S_ISREG(opened.st_mode) or _file_identity(opened) != _file_identity(observed):
            raise ValueError("Repository evidence policy changed before observation")
        if opened.st_size > _MAX_POLICY_BYTES:
            raise ValueError("Repository evidence policy exceeds its byte budget")
        data = stream.read(_MAX_POLICY_BYTES + 1)
        if len(data) > _MAX_POLICY_BYTES:
            raise ValueError("Repository evidence policy exceeds its byte budget")
        if _file_identity(os.fstat(stream.fileno())) != _file_identity(opened):
            raise ValueError("Repository evidence policy changed during observation")
    capture = _RepositoryPolicyCapture(path, _file_identity(opened), data)
    capture.verify()
    return capture


def _trusted_exclusions() -> bytes:
    private = GRADER_PRIVATE_SOURCE_ROOT.as_posix()
    patterns = (private, private + "/**", "**/" + private, "**/" + private + "/**")
    return (
        "\n"
        + "".join(
            f'\n[[rule]]\npattern = "{pattern}"\nindex = false\nvisibility = "deny"\n'
            for pattern in patterns
        )
    ).encode("utf-8")


@dataclass(frozen=True, slots=True)
class RepositoryEvidenceScope:
    """The native handle and the exact consumer policy that bounds its lifetime."""

    native: CodeMap
    _root: _RepositoryRootCapture = field(repr=False)
    _policy: _RepositoryPolicyCapture = field(repr=False)

    def verify_policy(self) -> None:
        self._root.verify()
        if self.native.workspace != self._root.resolved:
            raise ValueError("Native repository evidence workspace differs from the selected root")
        self._policy.verify()


@contextmanager
def repository_evidence_scope(
    root: Path,
    *,
    state_dir: Path | None = None,
    artifact_db: Path | None = None,
) -> Iterator[RepositoryEvidenceScope]:
    """Keep native policy interpretation and private-source exclusion together.

    Policy drift fails the current observation rather than relabelling facts
    obtained under the captured policy. Hashmarks owns policy fingerprints and
    reconciliation of previously indexed members when this scope is reopened.
    """
    from hashmarks import CodeMap

    selected_root = _capture_root(root)
    policy = _capture_policy(selected_root.resolved)
    selected_root.verify()
    with tempfile.TemporaryDirectory(prefix="oh-goon-evidence-policy-") as directory:
        path = Path(directory) / "context.toml"
        path.write_bytes(policy.data + _trusted_exclusions())
        with CodeMap(
            selected_root.resolved, policy_path=path, state_dir=state_dir, artifact_db=artifact_db
        ) as native:
            scope = RepositoryEvidenceScope(native, selected_root, policy)
            scope.verify_policy()
            yield scope
            scope.verify_policy()
