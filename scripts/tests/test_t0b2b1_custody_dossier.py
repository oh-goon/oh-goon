"""T0B2B1: complete signed review coverage is NOT independent custody proof."""

from __future__ import annotations

import hashlib
import json
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
    expected_subjects,
)
from oh_goon.repository_tooling.offline_custody_review import (
    _DOMAIN,
    ReviewRejected,
    _canonical,
    inspect_dossier,
)


def _fixture(tmp_path: Path) -> tuple[Path, Path, str, tuple[str, ...]]:
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    key = tmp_path / "reviewer.pub"
    key.write_bytes(public)
    pin = hashlib.sha256(public).hexdigest()
    dossier = tmp_path / "dossier"
    subjects_root = dossier / "subjects"
    subjects_root.mkdir(parents=True)
    registry = json.loads((ROOT / _REGISTRY).read_bytes())
    subjects = expected_subjects(registry)
    for subject in subjects:
        leaf = hashlib.sha256(subject.encode()).hexdigest()
        target = subjects_root / leaf
        target.mkdir()
        evidence = f"synthetic observation for {subject}; NOT actual custodian evidence".encode()
        (target / "observation.txt").write_bytes(evidence)
        payload = {
            "schema": "oh-goon/offline-custody-review/v1",
            "policy_sha256": hashlib.sha256(
                (ROOT / _REGISTRY).read_bytes()
            ).hexdigest(),
            "custody_register_sha256": hashlib.sha256(
                (ROOT / "backend/trust-custody-evidence.json").read_bytes()
            ).hexdigest(),
            "subject": subject,
            "disposition": "KEEP",
            "reviewer_id": "synthetic-reviewer-unit-test",
            "rationale": "synthetic evidence only, no independent real-world custodian verified",
            "observations": {
                field: "synthetic unit-test statement" for field in _REVIEW_FIELDS
            },
            "evidence": {"observation.txt": hashlib.sha256(evidence).hexdigest()},
        }
        raw = _canonical(payload)
        (target / "review.json").write_bytes(raw)
        (target / "review.sig").write_text(private.sign(_DOMAIN + raw).hex() + "\n")
    return dossier, key, pin, subjects


def test_complete_signed_census_does_not_grant_trust(tmp_path: Path) -> None:
    dossier, key, pin, subjects = _fixture(tmp_path)
    result = inspect_dossier(dossier, key, pin)
    assert result["status"] == "COMPLETE_AUTHENTICATED_REVIEW_SET_NOT_CUSTODY_PROOF"
    assert result["reviewed_subject_count"] == len(subjects)
    assert result["gate_a"] == "BLOCKED"
    assert result["runtime_authority"] is False
    assert result["T1_migration_authorized"] is False
    assert result["independent_custody_verified"] is False
    assert result["approval"] == "NOT_GRANTED"
    assert len(result["review_dossier_sha256"]) == 64


@pytest.mark.parametrize(
    "attack",
    [
        "missing_subject",
        "unexpected_subject",
        "subject_substitution",
        "unsigned_evidence",
        "extra_evidence",
        "symlink_subject",
        "symlink_subjects_root",
        "changed_signature",
        "wrong_external_pin",
        "policy_identity_swap",
        "hardlink_packet",
        "reviewer_key_swap",
    ],
)
def test_dossier_rejects_incomplete_forged_or_aliased_sources(
    tmp_path: Path, attack: str
) -> None:
    dossier, key, pin, subjects = _fixture(tmp_path)
    folders = [
        dossier / "subjects" / hashlib.sha256(subject.encode()).hexdigest()
        for subject in subjects
    ]
    first, second = folders[:2]
    if attack == "missing_subject":
        shutil.rmtree(first)
    elif attack == "unexpected_subject":
        (dossier / "subjects" / ("0" * 64)).mkdir()
    elif attack == "subject_substitution":
        shutil.rmtree(first)
        shutil.copytree(second, first)
    elif attack == "unsigned_evidence":
        (first / "observation.txt").write_bytes(b"forged evidence")
    elif attack == "extra_evidence":
        (first / "unexpected.txt").write_bytes(b"not in the signed manifest")
    elif attack == "symlink_subject":
        first.rename(dossier / "old")
        first.symlink_to(dossier / "old", target_is_directory=True)
    elif attack == "symlink_subjects_root":
        (dossier / "subjects").rename(dossier / "renamed")
        (dossier / "subjects").symlink_to(dossier / "renamed", target_is_directory=True)
    elif attack == "changed_signature":
        (first / "review.sig").write_text("0" * 128 + "\n")
    elif attack == "wrong_external_pin":
        pin = "0" * 64
    elif attack == "policy_identity_swap":
        payload = json.loads((first / "review.json").read_text())
        payload["policy_sha256"] = "0" * 64
        (first / "review.json").write_bytes(_canonical(payload))
    elif attack == "hardlink_packet":
        os.link(first / "review.json", first / "other.json")
    elif attack == "reviewer_key_swap":
        key.write_bytes(os.urandom(32))
    with pytest.raises(ReviewRejected):
        inspect_dossier(dossier, key, pin)


def _architecture_fixture(tmp_path: Path) -> Path:
    for name in (
        _SOURCE,
        "backend/pyproject.toml",
        "scripts/qualify_b12_development_wsl.sh",
        "scripts/tests/test_t0b2_offline_custody_review.py",
        "scripts/tests/test_t0b2b1_custody_dossier.py",
    ):
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, target)
    return tmp_path


@pytest.mark.parametrize(
    "mutation",
    [
        "remove_original_verifier",
        "allow_incomplete_subjects",
        "allow_unlisted_evidence",
        "promote_custody",
        "remove_qualifier",
        "remove_dossier_tests",
    ],
)
def test_x0_blocks_dossier_policy_drift(tmp_path: Path, mutation: str) -> None:
    root = _architecture_fixture(tmp_path)
    assert not check_offline_custody_review_contract(root)
    source = root / _SOURCE
    if mutation == "remove_original_verifier":
        source.write_text(
            source.read_text().replace(
                "verified = inspect_packet(", "verified = bypass_review("
            )
        )
    elif mutation == "allow_incomplete_subjects":
        source.write_text(
            source.read_text().replace(
                "set(os.listdir(subjects_fd)) != set(names)", "False"
            )
        )
    elif mutation == "allow_unlisted_evidence":
        source.write_text(
            source.read_text().replace("set(os.listdir(fd)) != members", "False")
        )
    elif mutation == "promote_custody":
        source.write_text(
            source.read_text().replace(
                '"independent_custody_verified": False',
                '"independent_custody_verified": True',
            )
        )
    elif mutation == "remove_qualifier":
        shell = root / "scripts/qualify_b12_development_wsl.sh"
        shell.write_text(
            shell.read_text().replace(
                "--require-module test_t0b2b1_custody_dossier", ""
            )
        )
    elif mutation == "remove_dossier_tests":
        (root / "scripts/tests/test_t0b2b1_custody_dossier.py").unlink()
    assert check_offline_custody_review_contract(root)


def _resign_dossier(
    dossier: Path, key: Ed25519PrivateKey, subjects: tuple[str, ...]
) -> None:
    for subject in subjects:
        folder = dossier / "subjects" / hashlib.sha256(subject.encode()).hexdigest()
        (folder / "review.sig").write_text(
            key.sign(_DOMAIN + (folder / "review.json").read_bytes()).hex() + "\n"
        )


def test_comparison_of_distinct_signed_sets_still_never_proves_custody(
    tmp_path: Path,
) -> None:
    from oh_goon.repository_tooling.offline_custody_review import (
        compare_review_dossiers,
    )

    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    first, key1, pin1, subjects = _fixture(a)
    second, key2, pin2, _ = _fixture(b)
    result = compare_review_dossiers(first, key1, pin1, second, key2, pin2)
    assert result["status"] == "AUTHENTICATED_REVIEW_AGREEMENT_NOT_CUSTODY_PROOF"
    assert result["reviewer_keys_distinct"] is True
    assert result["independent_custody_verified"] is False
    assert result["gate_a"] == "BLOCKED"
    with pytest.raises(ReviewRejected):
        compare_review_dossiers(first, key1, pin1, first, key1, pin1)


def test_conflicting_fully_signed_review_sets_fail_closed(tmp_path: Path) -> None:
    from oh_goon.repository_tooling.offline_custody_review import (
        compare_review_dossiers,
    )

    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    first, key1, pin1, subjects = _fixture(a)
    second, key2, pin2, _ = _fixture(b)
    target = second / "subjects" / hashlib.sha256(subjects[0].encode()).hexdigest()
    content = json.loads((target / "review.json").read_bytes())
    content["disposition"] = "MERGE"
    raw = _canonical(content)
    (target / "review.json").write_bytes(raw)
    # Another valid reviewer signs a disagreeing decision; a cryptographic PASS
    # alone must not silently make it the trusted disposition.
    private = Ed25519PrivateKey.generate()
    key2.write_bytes(private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))
    pin2 = hashlib.sha256(key2.read_bytes()).hexdigest()
    _resign_dossier(second, private, subjects)
    with pytest.raises(ReviewRejected, match="disposition conflict"):
        compare_review_dossiers(first, key1, pin1, second, key2, pin2)
