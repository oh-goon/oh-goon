"""M163 non-live signer-side custody for native Enforcer/LACE authority issuers.

This is NOT a public signing endpoint, a credential generator, or an Enforcer API.
The *trusted native issuer* supplies the owner-observation callback; no Run/Plan
input may choose the callback, key directory, signing policy or trust roots.
Only the callback receives the short-lived Ed25519 key; the result is a claim.
"""

from __future__ import annotations

import json
import os
import re
import stat
from collections.abc import Callable, Mapping
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from oh_goon.core.enforcer_lace_issuer_evidence import (
    AUTHORITY_FIELDS,
    ISSUER_PROTOCOL,
    SIGNATURE_DOMAIN,
    IssuerEvidence,
)
from oh_goon.core.enforcer_lace_trust import (
    AdmittedRunContract,
    TrustProtocolError,
    canonical_bytes,
)

SIGNER_POLICY_SCHEMA = "oh-goon/deployment-issuer-signers/v1"
_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}\Z")
_POLICY_FILE = "signers.json"


def _secure_file(info: os.stat_result, *, directory: bool) -> None:
    if (
        info.st_uid != os.geteuid()
        or info.st_mode & 0o077
        or (not directory and info.st_nlink != 1)
        or not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
    ):
        raise TrustProtocolError("signer custody requires owner-only regular files and directory")


def _read_leaf(root_fd: int, filename: str, *, limit: int) -> tuple[bytes, tuple[int, ...]]:
    if not isinstance(filename, str) or not _NAME.fullmatch(filename) or filename in {".", ".."}:
        raise TrustProtocolError("invalid signer authority filename")
    fd: int | None = None
    try:
        fd = os.open(
            filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=root_fd
        )
        before = os.fstat(fd)
        _secure_file(before, directory=False)
        if not 0 < before.st_size <= limit:
            raise TrustProtocolError("unexpected signer authority file length")
        data = os.read(fd, limit + 1)
        after = os.fstat(fd)
        stamp = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        # A stable open descriptor does NOT prove the filename still names that
        # inode. Detect rename/replacement (including identical bytes) before
        # trusting a policy or private seed read.
        named = os.stat(filename, dir_fd=root_fd, follow_symlinks=False)
        if (
            stamp
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
            or stamp
            != (named.st_dev, named.st_ino, named.st_size, named.st_mtime_ns, named.st_ctime_ns)
            or before.st_mode != named.st_mode
            or before.st_uid != named.st_uid
            or before.st_nlink != named.st_nlink
            or len(data) != before.st_size
        ):
            raise TrustProtocolError("signer authority name changed during read")
        return data, stamp
    except OSError as exc:
        raise TrustProtocolError("signer authority file unavailable") from exc
    finally:
        if fd is not None:
            os.close(fd)


def _policy(root_fd: int, roots: Mapping[str, tuple[str, bytes]]) -> tuple[dict, tuple[int, ...]]:
    raw, stamp = _read_leaf(root_fd, _POLICY_FILE, limit=16384)
    try:
        policy = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise TrustProtocolError("invalid signer policy") from exc
    if (
        not isinstance(policy, dict)
        or set(policy) != {"schema", "issuers"}
        or policy["schema"] != SIGNER_POLICY_SCHEMA
        or not isinstance(policy["issuers"], dict)
        or set(policy["issuers"]) != AUTHORITY_FIELDS
        or set(roots) != AUTHORITY_FIELDS
    ):
        raise TrustProtocolError("exact deployment signer policy and public roots required")
    filenames: set[str] = {_POLICY_FILE}
    identities: set[str] = set()
    public_keys: set[str] = set()
    key_ids: set[str] = set()
    for field in sorted(AUTHORITY_FIELDS):
        config = policy["issuers"][field]
        if not isinstance(config, dict) or set(config) != {
            "issuer_id",
            "key_id",
            "private_key_file",
            "public_key_hex",
            "state",
        }:
            raise TrustProtocolError("incomplete signer policy")
        issuer_id = config["issuer_id"]
        key_id = config["key_id"]
        filename = config["private_key_file"]
        pub_hex = config["public_key_hex"]
        if (
            not all(
                isinstance(value, str) and _NAME.fullmatch(value)
                for value in (issuer_id, key_id, filename)
            )
            or filename in {".", "..", _POLICY_FILE}
            or not isinstance(pub_hex, str)
            or len(pub_hex) != 64
            or pub_hex != pub_hex.lower()
            or any(c not in "0123456789abcdef" for c in pub_hex)
            or config["state"] not in {"active", "revoked"}
        ):
            raise TrustProtocolError("invalid deployment signer identity, key or state")
        root = roots[field]
        if (
            not isinstance(root, tuple)
            or len(root) != 2
            or root[0] != issuer_id
            or type(root[1]) is not bytes
            or root[1].hex() != pub_hex
        ):
            raise TrustProtocolError("signer policy does not match independent public trust root")
        filenames.add(filename)
        identities.add(issuer_id)
        public_keys.add(pub_hex)
        key_ids.add(key_id)
    if (
        not all(
            len(values) == len(AUTHORITY_FIELDS) for values in (identities, public_keys, key_ids)
        )
        or len(filenames) != len(AUTHORITY_FIELDS) + 1
    ):
        raise TrustProtocolError("signer key identities and files must be independent")
    if canonical_bytes(policy) != raw:
        raise TrustProtocolError("signer policy must use canonical encoding")
    return policy, stamp


def issue_with_deployment_signer(
    *,
    key_directory: Path,
    trusted_public_roots: Mapping[str, tuple[str, bytes]],
    field: str,
    contract: AdmittedRunContract,
    native_issuer: Callable[[str, Ed25519PrivateKey], IssuerEvidence],
) -> IssuerEvidence:
    """Run a *trusted* native owner issuer with a deployment-pinned active key.

    There is no caller-provided payload/signature primitive. This adapter does not
    select the native observer, check physical enforcement, or admit a Run. It
    cannot be exposed as an API accepting user-selected ``native_issuer`` values.
    Rotation requires a new explicit key/issuer identity and matched public roots;
    revoked entries always reject, including previously cached callers.
    """
    contract.verify()
    if (
        field not in AUTHORITY_FIELDS
        or not callable(native_issuer)
        or not isinstance(key_directory, Path)
        or not key_directory.is_absolute()
        or os.name != "posix"
        or not hasattr(os, "O_NOFOLLOW")
    ):
        raise TrustProtocolError("explicit POSIX native issuer and signing authority required")
    root_fd: int | None = None
    try:
        root_fd = os.open(
            key_directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        )
        directory_info = os.fstat(root_fd)
        _secure_file(directory_info, directory=True)
        directory_identity = (directory_info.st_dev, directory_info.st_ino)
        before, before_stamp = _policy(root_fd, trusted_public_roots)
        chosen = before["issuers"][field]
        if chosen["state"] != "active":
            raise TrustProtocolError("issuer signing authority is revoked")
        raw, private_stamp = _read_leaf(root_fd, chosen["private_key_file"], limit=32)
        if len(raw) != 32:
            raise TrustProtocolError("exact Ed25519 raw private seed required")
        private_key = Ed25519PrivateKey.from_private_bytes(raw)
        public = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        if public != trusted_public_roots[field][1]:
            raise TrustProtocolError("issuer signing seed differs from pinned public root")
        # Only the trusted native observer may see the key. It must issue a claim
        # about its native owner, not merely sign a caller-provided identity.
        claim = native_issuer(chosen["issuer_id"], private_key)
        if (
            not isinstance(claim, IssuerEvidence)
            or claim.field != field
            or claim.issuer_id != chosen["issuer_id"]
        ):
            raise TrustProtocolError("native issuer returned wrong signing domain")
        fields = contract.fields
        expected = canonical_bytes(
            {
                "protocol": ISSUER_PROTOCOL,
                "field": field,
                "run_id": fields["run_id"],
                "contract_identity": contract.identity,
                "identity": fields[field],
                "executor_generation": fields["executor_generation"],
            }
        )
        if claim.payload != expected or not isinstance(claim.signature_hex, str):
            raise TrustProtocolError("native issuer signed a different contract")
        try:
            if (
                len(claim.signature_hex) != 128
                or claim.signature_hex != claim.signature_hex.lower()
            ):
                raise ValueError("noncanonical signature")
            Ed25519PublicKey.from_public_bytes(public).verify(
                bytes.fromhex(claim.signature_hex),
                SIGNATURE_DOMAIN + field.encode("ascii") + b"\x00" + claim.payload,
            )
        except (ValueError, InvalidSignature) as exc:
            raise TrustProtocolError("native issuer produced an invalid signature") from exc
        # A policy replacement/revocation, private-key mutation or filesystem
        # name replacement before returning evidence aborts issuance.
        after, after_stamp = _policy(root_fd, trusted_public_roots)
        current_directory = os.stat(key_directory, follow_symlinks=False)
        if (
            after != before
            or after_stamp != before_stamp
            or (current_directory.st_dev, current_directory.st_ino) != directory_identity
            or _read_leaf(root_fd, chosen["private_key_file"], limit=32)[1] != private_stamp
        ):
            raise TrustProtocolError("signer authority changed during issuance")
        return claim
    except OSError as exc:
        raise TrustProtocolError("deployment signer custody unavailable") from exc
    finally:
        if root_fd is not None:
            os.close(root_fd)


def deployment_signer_policy_snapshot(
    *,
    key_directory: Path,
    trusted_public_roots: Mapping[str, tuple[str, bytes]],
) -> tuple[tuple[int, int], tuple[int, ...], bytes]:
    """Observe the deployment signer generation without disclosing private keys.

    The same owned file reader and canonical policy verifier used for individual
    signing operations is authoritative.  Comparing snapshots across a six-claim
    bundle detects rotation/revocation and directory or policy replacement.
    """
    if (
        not isinstance(key_directory, Path)
        or not key_directory.is_absolute()
        or os.name != "posix"
        or not hasattr(os, "O_NOFOLLOW")
    ):
        raise TrustProtocolError("explicit deployment signer directory required")
    root_fd: int | None = None
    try:
        root_fd = os.open(
            key_directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        )
        before = os.fstat(root_fd)
        _secure_file(before, directory=True)
        policy, stamp = _policy(root_fd, trusted_public_roots)
        # The signer policy alone is not proof that its six native signing
        # seeds are available. Validate every exact private key inside the
        # existing signer owner, without returning/exporting key bytes and
        # without issuing a claim. Missing, mismatched and substituted seeds
        # must prevent Fleet prework before a lease or Journal write.
        leaf_stamps: dict[str, tuple[int, ...]] = {}
        for field in sorted(AUTHORITY_FIELDS):
            filename = policy["issuers"][field]["private_key_file"]
            raw, leaf_stamp = _read_leaf(root_fd, filename, limit=32)
            if len(raw) != 32:
                raise TrustProtocolError("exact Ed25519 signing seed required")
            actual_public = (
                Ed25519PrivateKey.from_private_bytes(raw)
                .public_key()
                .public_bytes(
                    encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
                )
            )
            if actual_public != trusted_public_roots[field][1]:
                raise TrustProtocolError("native signer seed differs from independent public root")
            leaf_stamps[filename] = leaf_stamp
        # Fence a policy replacement or any leaf rename occurring *after*
        # its individual read but before this six-key observation completes.
        final_policy, final_stamp = _policy(root_fd, trusted_public_roots)
        if final_policy != policy or final_stamp != stamp:
            raise TrustProtocolError("signer policy changed during custody observation")
        for filename, expected in leaf_stamps.items():
            named_leaf = os.stat(filename, dir_fd=root_fd, follow_symlinks=False)
            actual = (
                named_leaf.st_dev,
                named_leaf.st_ino,
                named_leaf.st_size,
                named_leaf.st_mtime_ns,
                named_leaf.st_ctime_ns,
            )
            if (
                actual != expected
                or not stat.S_ISREG(named_leaf.st_mode)
                or named_leaf.st_uid != os.geteuid()
                or named_leaf.st_mode & 0o077
                or named_leaf.st_nlink != 1
            ):
                raise TrustProtocolError("signer leaf changed during custody observation")
        after = os.fstat(root_fd)
        named = os.stat(key_directory, follow_symlinks=False)
        if (
            (before.st_dev, before.st_ino, before.st_ctime_ns)
            != (after.st_dev, after.st_ino, after.st_ctime_ns)
            or (after.st_dev, after.st_ino, after.st_ctime_ns)
            != (named.st_dev, named.st_ino, named.st_ctime_ns)
            or after.st_uid != named.st_uid
            or after.st_mode != named.st_mode
        ):
            raise TrustProtocolError("signer custody directory changed during observation")
        return (before.st_dev, before.st_ino), stamp, canonical_bytes(policy)
    except OSError as exc:
        raise TrustProtocolError("deployment signer policy authority unavailable") from exc
    finally:
        if root_fd is not None:
            os.close(root_fd)
