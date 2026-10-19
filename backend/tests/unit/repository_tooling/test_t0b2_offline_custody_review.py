"""Hostile T0B2A offline review tests; never actual independent custody evidence."""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from oh_goon.repository_tooling.architecture.offline_custody_review_contract import (
    _SOURCE,
    check_offline_custody_review_contract,
)
from oh_goon.repository_tooling.architecture.repository_layout import ROOT
from oh_goon.repository_tooling.architecture.trust_custody_evidence_contract import (
    _REGISTRY,
    _REVIEW_FIELDS,
)
from oh_goon.repository_tooling.offline_custody_review import (
    _DOMAIN,
    ReviewRejected,
    _canonical,
    inspect_packet,
)


def _packet(tmp_path: Path):
    directory = tmp_path / "packet"
    directory.mkdir()
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    key_path = tmp_path / "reviewer.pub"
    key_path.write_bytes(public)
    pin = hashlib.sha256(public).hexdigest()
    original = b"synthetic unit-test evidence; NOT evidence of real custody"
    (directory / "observations.txt").write_bytes(original)
    payload = {
        "schema": "oh-goon/offline-custody-review/v1",
        "policy_sha256": hashlib.sha256((ROOT / _REGISTRY).read_bytes()).hexdigest(),
        "custody_register_sha256": hashlib.sha256(
            (ROOT / "backend/trust-custody-evidence.json").read_bytes()
        ).hexdigest(),
        "subject": "admission_issuer_evidence/source_identity",
        "disposition": "KEEP",
        "reviewer_id": "test-only-reviewer",
        "rationale": "synthetic unit test only, no actual deployment is inspected",
        "observations": {key: "synthetic test statement" for key in _REVIEW_FIELDS},
        "evidence": {"observations.txt": hashlib.sha256(original).hexdigest()},
    }

    def sign():
        packet = _canonical(payload)
        (directory / "review.json").write_bytes(packet)
        (directory / "review.sig").write_text(private.sign(_DOMAIN + packet).hex() + "\n")

    sign()
    return directory, key_path, pin, payload, sign


def test_valid_signed_packet_is_only_intake_not_trust(tmp_path: Path) -> None:
    directory, key, pin, _, _ = _packet(tmp_path)
    result = inspect_packet(directory, key, pin)
    assert result["status"] == "AUTHENTICATED_REVIEW_PACKET_NOT_CUSTODY_PROOF"
    assert result["gate_a"] == "BLOCKED"
    assert result["runtime_authority"] is False
    assert result["T1_migration_authorized"] is False
    assert result["approval"] == "NOT_GRANTED"


@pytest.mark.parametrize(
    "attack",
    [
        "wrong_pin",
        "wrong_key",
        "changed_review",
        "changed_evidence",
        "wrong_policy",
        "wrong_register",
        "unregistered_subject",
        "unreviewed",
        "added_field",
        "incomplete_observations",
        "missing_rationale",
        "no_artifacts",
        "path_escape",
        "private_material",
        "bad_domain_signature",
        "unsupported_decision",
        "unsigned_claim_addition",
    ],
)
def test_forgery_substitution_and_schema_drift_fail(tmp_path: Path, attack: str) -> None:
    directory, key, pin, payload, sign = _packet(tmp_path)
    if attack == "wrong_pin":
        pin = "0" * 64
    elif attack == "wrong_key":
        key.write_bytes(os.urandom(32))
    elif attack == "changed_review":
        (directory / "review.json").write_bytes((directory / "review.json").read_bytes() + b"\n")
    elif attack == "changed_evidence":
        (directory / "observations.txt").write_bytes(b"attacker substitution")
    elif attack == "wrong_register":
        payload["custody_register_sha256"] = "0" * 64
        sign()
    elif attack == "wrong_policy":
        payload["policy_sha256"] = "0" * 64
        sign()
    elif attack == "unregistered_subject":
        payload["subject"] = "unregistered/signature_root"
        sign()
    elif attack == "unreviewed":
        payload["observations"]["semantic_owner"] = "NOT_OBSERVED"
        sign()
    elif attack == "added_field":
        payload["gate_a"] = "PASS"
        sign()
    elif attack == "incomplete_observations":
        del payload["observations"]["rotation"]
        sign()
    elif attack == "missing_rationale":
        payload["rationale"] = ""
        sign()
    elif attack == "no_artifacts":
        payload["evidence"] = {}
        sign()
    elif attack == "path_escape":
        payload["evidence"] = {"../escaped": hashlib.sha256(b"x").hexdigest()}
        sign()
    elif attack == "private_material":
        value = b"-----BEGIN PRIVATE KEY-----\nfake\n"
        (directory / "observations.txt").write_bytes(value)
        payload["evidence"]["observations.txt"] = hashlib.sha256(value).hexdigest()
        sign()
    elif attack == "bad_domain_signature":
        (directory / "review.sig").write_text("0" * 128 + "\n")
    elif attack == "unsupported_decision":
        payload["disposition"] = "PASS"
        sign()
    elif attack == "unsigned_claim_addition":
        payload["approved_by_enforcer"] = "YES"
        sign()
    with pytest.raises(ReviewRejected):
        inspect_packet(directory, key, pin)


@pytest.mark.parametrize(
    "attack", ["symlink_evidence", "hardlink_evidence", "symlink_key", "symlink_packet"]
)
def test_evidence_object_aliases_rejected(tmp_path: Path, attack: str) -> None:
    directory, key, pin, _, _ = _packet(tmp_path)
    if attack == "symlink_evidence":
        source = directory / "observations.txt"
        source.rename(directory / "original.txt")
        source.symlink_to(directory / "original.txt")
    elif attack == "hardlink_evidence":
        os.link(directory / "observations.txt", directory / "another.txt")
    elif attack == "symlink_key":
        key.rename(tmp_path / "other.pub")
        key.symlink_to(tmp_path / "other.pub")
    elif attack == "symlink_packet":
        source = directory / "review.json"
        source.rename(directory / "other.json")
        source.symlink_to(directory / "other.json")
    with pytest.raises(ReviewRejected):
        inspect_packet(directory, key, pin)


def _architecture_fixture(tmp_path: Path) -> Path:
    for source in (
        _SOURCE,
        "backend/pyproject.toml",
        "scripts/qualify_b12_development_wsl.sh",
        "backend/tests/unit/repository_tooling/test_t0b2_offline_custody_review.py",
        "backend/tests/unit/repository_tooling/test_t0b2b1_custody_dossier.py",
    ):
        path = tmp_path / source
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / source, path)
    return tmp_path


@pytest.mark.parametrize(
    "mutation",
    [
        "remove_signature",
        "dead_branch_signature",
        "remove_no_follow",
        "grant_gate",
        "duplicate_runtime",
        "remove_enrollment",
    ],
)
def test_x0_rejects_offline_review_authority_drift(tmp_path: Path, mutation: str) -> None:
    root = _architecture_fixture(tmp_path)
    assert not check_offline_custody_review_contract(root)
    path = root / _SOURCE
    if mutation == "remove_signature":
        path.write_text(
            path.read_text().replace(
                "_verify_review_signature(packet,", "_bypass_signature(packet,"
            )
        )
    elif mutation == "dead_branch_signature":
        path.write_text(
            path.read_text().replace(
                "            _verify_review_signature(packet, bytes.fromhex(sig_hex.strip()), public_key)",
                "            if False:\n                _verify_review_signature(packet, bytes.fromhex(sig_hex.strip()), public_key)",
            )
        )
    elif mutation == "remove_no_follow":
        path.write_text(path.read_text().replace("os.O_NOFOLLOW", "os.O_RDONLY"))
    elif mutation == "grant_gate":
        path.write_text(
            path.read_text().replace(
                '"gate_a": "BLOCKED"',
                '"gate_a": "PASS"',
            )
        )
    elif mutation == "duplicate_runtime":
        p = root / "backend/src/oh_goon/runtime/second_trust_reviewer.py"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("from oh_goon.repository_tooling import offline_custody_review\n")
    elif mutation == "remove_enrollment":
        shell = root / "scripts/qualify_b12_development_wsl.sh"
        shell.write_text(
            shell.read_text().replace("--require-module test_t0b2_offline_custody_review", "")
        )
    assert check_offline_custody_review_contract(root)
