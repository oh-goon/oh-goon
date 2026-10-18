"""T0B2A offline evidence intake. NEVER a Run/admission/Gate A trust authority.

The independent reviewer public key AND its expected fingerprint must be provided
out of band. A valid signature authenticates a submitted review packet, NOT its
real-world claims of independent custody. The original pending census remains
unaltered; approval and live policy migration are separate T0B2/T1/T2 gates.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path

from oh_goon.repository_tooling.architecture.repository_layout import ROOT
from oh_goon.repository_tooling.architecture.trust_custody_evidence_contract import (
    _REGISTRY,
    _REVIEW_FIELDS,
    expected_subjects,
)

_DOMAIN = b"oh-goon/offline-custody-review/v1\x00"
_SCHEMA = "oh-goon/offline-custody-review/v1"
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_FILE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}\Z")
_MAX_EVIDENCE = 1024 * 1024
_MAX_PACKET = 64 * 1024


class ReviewRejected(ValueError):
    """Intake rejected: no effect or trust decision is authorized."""


def _canonical(payload: dict) -> bytes:
    return (json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode()


def _read_bounded(root_fd: int, leaf: str, max_bytes: int) -> bytes:
    """FD-relative immutable identity read; reject symbolic/hardlink aliases."""
    if not _FILE.fullmatch(leaf) or leaf in {".", ".."}:
        raise ReviewRejected("invalid evidence filename")
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
    try:
        fd = os.open(leaf, flags, dir_fd=root_fd)
        try:
            before = os.fstat(fd)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_nlink != 1
                or before.st_size > max_bytes
            ):
                raise ReviewRejected("evidence is not a bounded single-link regular file")
            with os.fdopen(os.dup(fd), "rb") as stream:
                data = stream.read(max_bytes + 1)
            after = os.fstat(fd)

            def identity(s):
                return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)

            if (
                len(data) > max_bytes
                or len(data) != before.st_size
                or identity(before) != identity(after)
            ):
                raise ReviewRejected("evidence mutated during inspection")
            return data
        finally:
            os.close(fd)
    except OSError as exc:
        raise ReviewRejected(f"cannot read named evidence: {leaf}") from exc


def _read_external_key(path: Path) -> bytes:
    parent_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        key = _read_bounded(parent_fd, path.name, 32)
    finally:
        os.close(parent_fd)
    if len(key) != 32:
        raise ReviewRejected("reviewer key must be raw 32-byte Ed25519 public key")
    return key


def _verify_review_signature(packet: bytes, signature: bytes, public_key: bytes) -> None:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(signature, _DOMAIN + packet)
    except (InvalidSignature, ValueError) as exc:
        raise ReviewRejected("offline reviewer signature invalid") from exc


def inspect_packet(
    packet_dir: Path | None,
    reviewer_public_key: Path,
    expected_key_sha256: str,
    *,
    policy_root: Path = ROOT,
    packet_fd: int | None = None,
) -> dict:
    """Authenticate *intake only*. Never returns a trust grant, Gate A PASS or T1 decision."""
    if not _HEX.fullmatch(expected_key_sha256):
        raise ReviewRejected("externally pinned reviewer key digest is required")
    try:
        public_key = _read_external_key(reviewer_public_key)
        if hashlib.sha256(public_key).hexdigest() != expected_key_sha256:
            raise ReviewRejected("reviewer public key does not match out-of-band pin")
        directory_fd = (
            os.dup(packet_fd)
            if packet_fd is not None
            else os.open(packet_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        )
        try:
            packet = _read_bounded(directory_fd, "review.json", _MAX_PACKET)
            sig_hex = _read_bounded(directory_fd, "review.sig", 129).decode("ascii")
            if not re.fullmatch(r"[0-9a-f]{128}\n?", sig_hex):
                raise ReviewRejected("detached signature must be canonical lowercase hex")
            _verify_review_signature(packet, bytes.fromhex(sig_hex.strip()), public_key)
            payload = json.loads(packet)
            if not isinstance(payload, dict) or packet != _canonical(payload):
                raise ReviewRejected("review packet is not canonical JSON")
            if (
                set(payload)
                != {
                    "schema",
                    "policy_sha256",
                    "custody_register_sha256",
                    "subject",
                    "disposition",
                    "reviewer_id",
                    "observations",
                    "evidence",
                    "rationale",
                }
                or payload["schema"] != _SCHEMA
            ):
                raise ReviewRejected("unknown or incomplete review packet schema")
            registry = json.loads((policy_root / _REGISTRY).read_bytes())
            policy_digest = hashlib.sha256((policy_root / _REGISTRY).read_bytes()).hexdigest()
            review_register_digest = hashlib.sha256(
                (policy_root / "backend/trust-custody-evidence.json").read_bytes()
            ).hexdigest()
            if payload["custody_register_sha256"] != review_register_digest:
                raise ReviewRejected("review packet bound to a different pending custody register")
            if payload["policy_sha256"] != policy_digest:
                raise ReviewRejected("review packet bound to a different authority inventory")
            if payload["subject"] not in expected_subjects(registry):
                raise ReviewRejected("review subject is not registered")
            if payload["disposition"] not in {"KEEP", "MERGE", "EXTERNAL", "REMOVE"}:
                raise ReviewRejected("unsupported review disposition")
            if not isinstance(payload["reviewer_id"], str) or not (
                1 <= len(payload["reviewer_id"]) <= 128
            ):
                raise ReviewRejected("reviewer identity label missing")
            if not isinstance(payload["rationale"], str) or not (
                10 <= len(payload["rationale"]) <= 2000
            ):
                raise ReviewRejected("review rationale missing")
            observations = payload["observations"]
            if not isinstance(observations, dict) or set(observations) != set(_REVIEW_FIELDS):
                raise ReviewRejected("incomplete custody observations")
            if any(
                not isinstance(value, str) or not 1 <= len(value) <= 500 or value == "NOT_OBSERVED"
                for value in observations.values()
            ):
                raise ReviewRejected("custody observations must contain explicit review statements")
            evidence = payload["evidence"]
            if not isinstance(evidence, dict) or not 1 <= len(evidence) <= 20:
                raise ReviewRejected("at least one bounded evidence artifact is required")
            total = 0
            for leaf, expected in sorted(evidence.items()):
                if (
                    not isinstance(leaf, str)
                    or not isinstance(expected, str)
                    or not _HEX.fullmatch(expected)
                ):
                    raise ReviewRejected("invalid evidence member manifest")
                if leaf in {"review.json", "review.sig"}:
                    raise ReviewRejected("signature cannot be its own evidence")
                data = _read_bounded(directory_fd, leaf, _MAX_EVIDENCE)
                total += len(data)
                if total > 4 * _MAX_EVIDENCE or hashlib.sha256(data).hexdigest() != expected:
                    raise ReviewRejected("evidence bytes differ from signed digest")
                if (
                    b"-----BEGIN PRIVATE KEY-----" in data
                    or b"-----BEGIN OPENSSH PRIVATE KEY-----" in data
                ):
                    raise ReviewRejected("private key material forbidden in review evidence")
            return {
                "schema": "oh-goon/offline-custody-intake-result/v1",
                "status": "AUTHENTICATED_REVIEW_PACKET_NOT_CUSTODY_PROOF",
                "review_subject": payload["subject"],
                "proposed_disposition": payload["disposition"],
                "packet_sha256": hashlib.sha256(packet).hexdigest(),
                "reviewer_public_key_sha256": expected_key_sha256,
                "evidence_file_count": len(evidence),
                "gate_a": "BLOCKED",
                "runtime_authority": False,
                "approval": "NOT_GRANTED",
                "T1_migration_authorized": False,
            }
        finally:
            os.close(directory_fd)
    except (OSError, UnicodeError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise ReviewRejected("offline review packet invalid or unreadable") from exc


def inspect_dossier(
    dossier_dir: Path,
    reviewer_public_key: Path,
    expected_key_sha256: str,
    *,
    policy_root: Path = ROOT,
) -> dict:
    """Reconcile ALL registered subjects via the existing per-packet verifier.

    One complete signed submission is still neither independent corroboration nor
    real deployment custody proof. Never returns an admission/migration decision.
    """
    try:
        registry = json.loads((policy_root / _REGISTRY).read_bytes())
        expected = expected_subjects(registry)
        names = {
            hashlib.sha256(subject.encode("utf-8")).hexdigest(): subject for subject in expected
        }
        if len(names) != len(expected):
            raise ReviewRejected("subject-directory identity collision")
        root_fd = os.open(dossier_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            subjects_fd = os.open(
                "subjects", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd
            )
            try:
                if set(os.listdir(subjects_fd)) != set(names):
                    raise ReviewRejected(
                        "dossier must include exactly all registered trust subjects"
                    )
                observed: list[tuple[str, str, str]] = []
                for leaf, subject in sorted(names.items(), key=lambda item: item[1]):
                    fd = os.open(
                        leaf, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=subjects_fd
                    )
                    try:
                        # No alternative cryptographic path. This is the SAME T0B2A verifier.
                        verified = inspect_packet(
                            None,
                            reviewer_public_key,
                            expected_key_sha256,
                            policy_root=policy_root,
                            packet_fd=fd,
                        )
                        if verified["review_subject"] != subject:
                            raise ReviewRejected(
                                "signed packet subject does not match canonical directory"
                            )
                        packet = json.loads(_read_bounded(fd, "review.json", _MAX_PACKET))
                        members = {"review.json", "review.sig", *packet["evidence"]}
                        if set(os.listdir(fd)) != members:
                            raise ReviewRejected("unlisted dossier evidence member")
                        observed.append(
                            (subject, verified["packet_sha256"], verified["proposed_disposition"])
                        )
                    finally:
                        os.close(fd)
            finally:
                os.close(subjects_fd)
        finally:
            os.close(root_fd)
        payload = {
            "subject_packets": observed,
            "policy_sha256": hashlib.sha256((policy_root / _REGISTRY).read_bytes()).hexdigest(),
        }
        return {
            "schema": "oh-goon/offline-custody-dossier/v1",
            "status": "COMPLETE_AUTHENTICATED_REVIEW_SET_NOT_CUSTODY_PROOF",
            "reviewed_subject_count": len(observed),
            "proposed_dispositions": {subject: proposed for subject, _, proposed in observed},
            "review_dossier_sha256": hashlib.sha256(_canonical(payload)).hexdigest(),
            "reviewer_public_key_sha256": expected_key_sha256,
            "gate_a": "BLOCKED",
            "runtime_authority": False,
            "approval": "NOT_GRANTED",
            "T1_migration_authorized": False,
            "independent_custody_verified": False,
        }
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        if isinstance(exc, ReviewRejected):
            raise
        raise ReviewRejected("custody dossier is invalid or unreadable") from exc


def compare_review_dossiers(
    primary_dir: Path,
    primary_key: Path,
    primary_pin: str,
    secondary_dir: Path,
    secondary_key: Path,
    secondary_pin: str,
    *,
    policy_root: Path = ROOT,
) -> dict:
    """Detect disagreements, not independent trust custody or reviewer independence.

    Two distinct review *keys* are not evidence of two real-world custodians.
    """
    if primary_pin == secondary_pin:
        raise ReviewRejected(
            "different independent reviewer key identities must be externally pinned"
        )
    primary = inspect_dossier(primary_dir, primary_key, primary_pin, policy_root=policy_root)
    secondary = inspect_dossier(
        secondary_dir, secondary_key, secondary_pin, policy_root=policy_root
    )
    if primary["proposed_dispositions"] != secondary["proposed_dispositions"]:
        raise ReviewRejected("reviewer disposition conflict; mandatory human reconciliation")
    return {
        "schema": "oh-goon/offline-custody-review-comparison/v1",
        "status": "AUTHENTICATED_REVIEW_AGREEMENT_NOT_CUSTODY_PROOF",
        "reviewed_subject_count": primary["reviewed_subject_count"],
        "primary_review_sha256": primary["review_dossier_sha256"],
        "secondary_review_sha256": secondary["review_dossier_sha256"],
        "reviewer_keys_distinct": True,
        "independent_custody_verified": False,
        "gate_a": "BLOCKED",
        "runtime_authority": False,
        "approval": "NOT_GRANTED",
        "T1_migration_authorized": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Offline independent custody evidence intake; no Gate A authority"
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--packet-dir", type=Path)
    source.add_argument("--dossier-dir", type=Path)
    parser.add_argument("--reviewer-public-key", type=Path, required=True)
    parser.add_argument("--expected-reviewer-key-sha256", required=True)
    parser.add_argument("--corroboration-dossier-dir", type=Path)
    parser.add_argument("--corroborator-public-key", type=Path)
    parser.add_argument("--expected-corroborator-key-sha256")
    args = parser.parse_args(argv)
    try:
        comparison = (
            args.corroboration_dossier_dir,
            args.corroborator_public_key,
            args.expected_corroborator_key_sha256,
        )
        if any(x is not None for x in comparison):
            if args.dossier_dir is None or not all(x is not None for x in comparison):
                raise ReviewRejected(
                    "comparison requires both complete dossiers and externally pinned reviewer keys"
                )
            result = compare_review_dossiers(
                args.dossier_dir,
                args.reviewer_public_key,
                args.expected_reviewer_key_sha256,
                *comparison,
            )
        elif args.dossier_dir is not None:
            result = inspect_dossier(
                args.dossier_dir, args.reviewer_public_key, args.expected_reviewer_key_sha256
            )
        else:
            result = inspect_packet(
                args.packet_dir, args.reviewer_public_key, args.expected_reviewer_key_sha256
            )
    except ReviewRejected as exc:
        print(
            json.dumps({"status": "BLOCKED", "reason": str(exc), "gate_a": "BLOCKED"}),
            file=sys.stderr,
        )
        return 3
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
