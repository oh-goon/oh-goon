"""T0A adversarial census tests; this is NOT native custody proof."""
from __future__ import annotations

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


def _minimal_tree(tmp_path: Path) -> Path:
    wanted = set(_SOURCES.values()) | set(_REQUIRED_CUSTODY_FILES) | {
        _REGISTRY, "backend/trust-custody-evidence.json", "docs/architecture/TRUST_AUTHORITY_CENSUS.md"
    }
    for relative in wanted:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)
    return tmp_path


def _change(path: Path, before: str, after: str) -> None:
    original = path.read_text(encoding="utf-8")
    assert before in original
    path.write_text(original.replace(before, after, 1), encoding="utf-8")


def _json_update(root: Path, transform) -> None:
    source = root / _REGISTRY
    payload = json.loads(source.read_text(encoding="utf-8"))
    transform(payload)
    source.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def test_original_registry_and_generated_census_are_exact() -> None:
    assert check_trust_authority_registry() == []
    assert (ROOT / "docs/architecture/TRUST_AUTHORITY_CENSUS.md").read_text() == render_trust_census()


def test_minimal_source_replica_accepts_without_a_new_authority(tmp_path: Path) -> None:
    assert check_trust_authority_registry(_minimal_tree(tmp_path)) == []


def test_existing_agent_tool_grant_cannot_disappear_from_the_source_census(tmp_path: Path) -> None:
    root = _minimal_tree(tmp_path)
    assert check_trust_authority_registry(root) == []
    _json_update(root, lambda payload: payload["families"]["control_plane_receipts"]
                 ["receipt_domains"].remove("agent-tool-launch-grant"))
    issues = check_trust_authority_registry(root)
    assert any("receipt domain census differs from live RECEIPT_DOMAINS" in issue.message
               for issue in issues)


@pytest.mark.parametrize("mutant", [
    "added_issuer", "removed_receipt_domain", "alternate_verifier",
    "fake_custody", "hidden_signer", "changed_domain_bytes",
    "duplicate_verifier", "removed_verifier_method", "altered_census",
    "policy_provenance", "new_issuer_source",
])
def test_source_registry_and_custody_mutations_fail_closed(tmp_path: Path, mutant: str) -> None:
    root = _minimal_tree(tmp_path)
    issuer = root / _SOURCES["admission_issuer_evidence"]
    receipts = root / _SOURCES["control_plane_receipts"]
    if mutant == "added_issuer":
        _change(issuer, '"source_identity", "lock_identity",',
                '"source_identity", "new_issuer_field", "lock_identity",')
    elif mutant == "removed_receipt_domain":
        _change(receipts, '"agent-source-prework-stage"', '"replacement-domain"')
    elif mutant == "alternate_verifier":
        _json_update(root, lambda p: p["families"]["admission_issuer_evidence"].update(
            verifier="NewIssuerVerifier.verify"))
    elif mutant == "fake_custody":
        _json_update(root, lambda p: p["families"]["admission_issuer_evidence"].update(
            custody_evidence="GATE_A_PASS"))
    elif mutant == "hidden_signer":
        _json_update(root, lambda p: p["families"]["signer_trust_epoch"].update(
            separate_cryptographic_signer=True))
    elif mutant == "changed_domain_bytes":
        _change(issuer, 'b"oh-goon/admission-issuer-ed25519/v2\\x00"',
                'b"oh-goon/admission-issuer-ed25519/v3\\x00"')
    elif mutant == "duplicate_verifier":
        extra = root / "backend/src/oh_goon/runtime/rogue_trust.py"
        extra.parent.mkdir(parents=True, exist_ok=True)
        extra.write_text("class TrustedIssuerVerifier: pass\n", encoding="utf-8")
    elif mutant == "removed_verifier_method":
        _change(receipts, "    def verify(self, receipt: AuthenticatedReceipt, *, domain: str)",
                "    def unauthorized_verify(self, receipt: AuthenticatedReceipt, *, domain: str)")
    elif mutant == "altered_census":
        (root / "docs/architecture/TRUST_AUTHORITY_CENSUS.md").write_text(
            "# new authority approved by developer\n", encoding="utf-8")
    elif mutant == "policy_provenance":
        _json_update(root, lambda p: p.update(policy_provenance="ATTESTED"))
    else:
        _json_update(root, lambda p: p["families"]["admission_issuer_evidence"].update(
            source="backend/src/oh_goon/core/rogue_verifier.py"))
    assert check_trust_authority_registry(root), mutant


def test_registry_is_explicitly_not_a_runtime_authority() -> None:
    payload = json.loads((ROOT / _REGISTRY).read_text())
    assert payload["status"] == "SOURCE_CENSUS_ONLY_NOT_RUNTIME_AUTHORITY"
    assert payload["gate_a"] == "BLOCKED"
    assert payload["policy_provenance"] == "NOT_ATTESTED"
    assert payload["deployment_custody"] == "NOT_OBSERVED"
