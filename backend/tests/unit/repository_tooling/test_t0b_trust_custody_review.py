"""T0B attack tests: a custody checklist is NEVER signing or admission authority."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from oh_goon.repository_tooling.architecture.repository_layout import ROOT
from oh_goon.repository_tooling.architecture.trust_authority_registry_contract import (
    _REGISTRY,
    _REQUIRED_CUSTODY_FILES,
    _SOURCES,
    check_trust_authority_registry,
    render_trust_census,
)
from oh_goon.repository_tooling.architecture.trust_custody_evidence_contract import (
    _CUSTODY,
    check_trust_custody_evidence,
    expected_subjects,
)


def _fixture(tmp_path: Path) -> Path:
    for relative in (
        set(_SOURCES.values())
        | set(_REQUIRED_CUSTODY_FILES)
        | {
            _REGISTRY,
            _CUSTODY,
            "docs/architecture/TRUST_AUTHORITY_CENSUS.md",
        }
    ):
        dest = tmp_path / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, dest)
    return tmp_path


def _mutate(root: Path, transform) -> None:
    path = root / _CUSTODY
    payload = json.loads(path.read_text())
    transform(payload)
    path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n")


def test_real_source_has_one_census_and_all_custody_questions_open() -> None:
    assert not check_trust_authority_registry()
    assert not check_trust_custody_evidence()
    policy = json.loads((ROOT / _REGISTRY).read_text())
    review = json.loads((ROOT / _CUSTODY).read_text())
    assert set(review["subjects"]) == set(expected_subjects(policy))
    assert (
        len(review["subjects"])
        == len(policy["families"]["admission_issuer_evidence"]["claims"]) + 4
    )
    assert all(x["review_state"] == "UNREVIEWED" for x in review["subjects"].values())
    assert (
        ROOT / "docs/architecture/TRUST_AUTHORITY_CENSUS.md"
    ).read_text() == render_trust_census()
    assert "Gate A BLOCKED" in render_trust_census()


def test_minimal_tree_cannot_hide_review_register(tmp_path: Path) -> None:
    assert not check_trust_authority_registry(_fixture(tmp_path))
    (tmp_path / _CUSTODY).unlink()
    assert check_trust_authority_registry(tmp_path)


@pytest.mark.parametrize(
    "mutant",
    [
        "missing_claim",
        "made_up_claim",
        "missing_external_root",
        "missing_observer",
        "forged_verification",
        "decided_merge",
        "fake_evidence_reference",
        "fake_process_custodian",
        "missing_compromise_field",
        "new_unapproved_field",
        "promoted_gate",
        "runtime_enabled",
        "rewritten_schema",
        "rewritten_registry_digest",
        "noncanonical_json",
    ],
)
def test_custody_fabrication_and_drift_fail_original_architecture_gate(
    tmp_path: Path, mutant: str
) -> None:
    root = _fixture(tmp_path)
    subject = "admission_issuer_evidence/source_identity"

    def update(payload: dict) -> None:
        record = payload["subjects"][subject]
        if mutant == "missing_claim":
            del payload["subjects"][subject]
        elif mutant == "made_up_claim":
            payload["subjects"]["admission_issuer_evidence/new_source"] = record.copy()
        elif mutant == "missing_external_root":
            del payload["subjects"]["future_role/provisioning_root"]
        elif mutant == "missing_observer":
            del payload["subjects"]["future_role/independent_physical_observer"]
        elif mutant == "forged_verification":
            record["review_state"] = "VERIFIED"
        elif mutant == "decided_merge":
            record["disposition"] = "MERGE"
        elif mutant == "fake_evidence_reference":
            record["evidence_refs"] = ["unverified-deployment-document"]
        elif mutant == "fake_process_custodian":
            record["observations"]["os_process_principal"] = "root"
        elif mutant == "missing_compromise_field":
            del record["observations"]["compromise_boundary"]
        elif mutant == "new_unapproved_field":
            record["self_attested_pass"] = True
        elif mutant == "promoted_gate":
            payload["gate_a"] = "PASS"
        elif mutant == "runtime_enabled":
            payload["runtime_authority"] = True
        elif mutant == "rewritten_schema":
            payload["schema"] = "oh-goon/trust-custody-evidence/v2"
        elif mutant == "rewritten_registry_digest":
            payload["policy_sha256"] = "0" * 64

    if mutant != "noncanonical_json":
        _mutate(root, update)
    else:
        path = root / _CUSTODY
        path.write_text(path.read_text().replace('  "gate_a"', '    "gate_a"', 1))
    assert check_trust_custody_evidence(root), mutant
    assert check_trust_authority_registry(root), mutant


def test_registered_source_policy_change_invalidates_custody_receipt(tmp_path: Path) -> None:
    root = _fixture(tmp_path)
    registry = root / _REGISTRY
    source = json.loads(registry.read_text())
    source["parent_checkpoint"] = "unauthorized-new-policy"
    registry.write_text(json.dumps(source, sort_keys=True, indent=2) + "\n")
    assert (
        hashlib.sha256(registry.read_bytes()).hexdigest()
        != json.loads((root / _CUSTODY).read_text())["policy_sha256"]
    )
    assert check_trust_custody_evidence(root)
    assert check_trust_authority_registry(root)


def test_runtime_must_not_consume_unsigned_evidence_as_policy(tmp_path: Path) -> None:
    root = _fixture(tmp_path)
    rogue = root / "backend/src/oh_goon/runtime/rogue_custody.py"
    rogue.parent.mkdir(parents=True, exist_ok=True)
    rogue.write_text('CUSTODY = "backend/trust-custody-evidence.json"\n')
    assert check_trust_authority_registry(root)


def test_t0b_enrolled_in_only_existing_native_qualification_flow() -> None:
    shell = (ROOT / "scripts/qualify_b12_development_wsl.sh").read_text()
    assert ".checkpoint/T0B1_SOURCE.sha256" in shell
    assert "tests/unit/repository_tooling/test_t0b_trust_custody_review.py" in shell
    assert "--require-module test_t0b_trust_custody_review" in shell
    assert "uv sync" in shell  # existing QENV1 contract, no second qualification launcher
