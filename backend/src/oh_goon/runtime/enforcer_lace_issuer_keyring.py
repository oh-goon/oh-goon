"""Explicit verification-only deployment public keys for Enforcer issuer evidence.

This opens *only* pre-provisioned, independently owned public verification roots. It
never holds private keys, signs claims, generates credentials, discovers ambient keys or authorizes
execution. The deployment owner must supply an exact field -> key-file mapping.
"""

from __future__ import annotations

import os
import re
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from oh_goon.core.enforcer_lace_issuer_evidence import AUTHORITY_FIELDS, TrustedIssuerVerifier
from oh_goon.core.enforcer_lace_trust import TrustProtocolError

_KEY_NAME = re.compile(r"[a-zA-Z0-9_][a-zA-Z0-9_.-]{0,127}\Z")
_PUBLIC_KEY_BYTES = 32


def _file_identity(info: os.stat_result) -> tuple[int, ...]:
    """Identity of an opened root or a name, including in-place mutation."""
    return (
        info.st_dev,
        info.st_ino,
        info.st_uid,
        info.st_mode,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


@dataclass(frozen=True, slots=True)
class IssuerKeyReference:
    """Independent issuer identity and one leaf filename in an explicit key directory."""

    issuer_id: str
    filename: str


def load_explicit_issuer_verifier(
    key_directory: Path,
    references: Mapping[str, IssuerKeyReference],
    *,
    public_root_owner_uid: int | None = None,
) -> TrustedIssuerVerifier:
    """Load pinned public roots using dirfd-relative, name-stable opens.

    The key directory is a trusted deployment setting, not an arbitrary Run/Plan
    input. Symlink leaves, directory traversal, weak permissions, FIFOs, devices,
    missing/extra authority fields and duplicate key files fail before work.
    Legacy roots require owner-only permissions and the effective process UID.
    An explicitly pinned *different* OS UID supports independent deployment
    ownership of read-only public roots (non-writable by group/other).  Public
    roots do not confer issuer signing authority. No ambient UID discovery.
    """
    if os.name != "posix" or not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise TrustProtocolError("POSIX no-follow issuer root authority required")
    if not isinstance(key_directory, Path) or not key_directory.is_absolute():
        raise TrustProtocolError("explicit absolute issuer key directory required")
    observer_uid = os.geteuid()
    if public_root_owner_uid is not None and (
        type(public_root_owner_uid) is not int
        or public_root_owner_uid < 0
        or public_root_owner_uid == observer_uid
    ):
        raise TrustProtocolError(
            "independent public root owner must be an explicitly different OS principal"
        )
    if not isinstance(references, Mapping) or set(references) != AUTHORITY_FIELDS:
        raise TrustProtocolError("all six explicit issuer key references required")
    values = tuple(references.values())
    if any(
        not isinstance(v, IssuerKeyReference)
        or not _KEY_NAME.fullmatch(v.filename)
        or v.filename in {".", ".."}
        for v in values
    ):
        raise TrustProtocolError("invalid or path-bearing issuer key filename")
    if len({v.filename for v in values}) != len(AUTHORITY_FIELDS):
        raise TrustProtocolError("each issuer authority requires its own key file")

    directory_fd = None
    try:
        directory_fd = os.open(
            key_directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        )
        original_directory = os.fstat(directory_fd)
        _require_secure_file(original_directory, directory=True, owner_uid=public_root_owner_uid)
        roots: dict[str, tuple[str, bytes]] = {}
        # Capture every originally opened leaf. A valid descriptor is not proof
        # that the *configured name* still points at that descriptor when the
        # verifier is returned; rename(2) can replace a name during a read.
        leaf_identities: dict[str, tuple[int, ...]] = {}
        for field in sorted(AUTHORITY_FIELDS):
            ref = references[field]
            key_fd = None
            try:
                key_fd = os.open(
                    ref.filename,
                    os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
                    dir_fd=directory_fd,
                )
                meta = os.fstat(key_fd)
                _require_secure_file(meta, directory=False, owner_uid=public_root_owner_uid)
                if meta.st_size != _PUBLIC_KEY_BYTES:
                    raise TrustProtocolError("exact raw Ed25519 issuer public key required")
                secret = os.read(key_fd, meta.st_size + 1)
                after = os.fstat(key_fd)
                if _file_identity(after) != _file_identity(meta):
                    raise TrustProtocolError("issuer key changed during read")
                if len(secret) != meta.st_size:
                    raise TrustProtocolError("issuer key changed during read")
                roots[field] = (ref.issuer_id, secret)
                leaf_identities[ref.filename] = _file_identity(meta)
            finally:
                if key_fd is not None:
                    os.close(key_fd)
        # Verify both the original directory descriptor and its *current path*
        # after the full six-root cut. Reject symlink/path replacement and leaf
        # rename, even when the old opened inode remained valid while reading.
        if _file_identity(os.fstat(directory_fd)) != _file_identity(
            original_directory
        ) or _file_identity(os.stat(key_directory, follow_symlinks=False)) != _file_identity(
            original_directory
        ):
            raise TrustProtocolError("issuer root directory changed during observation")
        for filename, identity in leaf_identities.items():
            if (
                _file_identity(os.stat(filename, dir_fd=directory_fd, follow_symlinks=False))
                != identity
            ):
                raise TrustProtocolError("issuer root name changed during observation")
        if os.geteuid() != observer_uid:
            raise TrustProtocolError("issuer root observer principal changed during observation")
        return TrustedIssuerVerifier(roots)
    except OSError as exc:
        raise TrustProtocolError("issuer trust root cannot be securely opened") from exc
    finally:
        if directory_fd is not None:
            os.close(directory_fd)


def _require_secure_file(
    info: os.stat_result,
    *,
    directory: bool,
    owner_uid: int | None = None,
) -> None:
    # The owner may make the public root readable by the verifier's UID, but
    # never writable by it through group/other. Signer keys stay owner-only.
    expected_uid = os.geteuid() if owner_uid is None else owner_uid
    unsafe_permissions = 0o077 if owner_uid is None else 0o022
    if (
        info.st_uid != expected_uid
        or info.st_mode & unsafe_permissions
        or (not directory and info.st_nlink != 1)
        or not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
    ):
        raise TrustProtocolError(
            "issuer trust root requires owner-only regular-file/directory authority"
        )
