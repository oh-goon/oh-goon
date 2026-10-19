"""T0A design-time census guard for existing cryptographic authority surfaces.

No registry-driven Run admission is added here. T1 must migrate the *existing*
verifier exclusively, with authenticated policy provisioning, once Gate A's
actual owner/custody requirements have been independently reviewed.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

from oh_goon.repository_tooling.architecture.common import Violation
from oh_goon.repository_tooling.architecture.repository_layout import ROOT
from oh_goon.repository_tooling.architecture.trust_custody_evidence_contract import (
    check_trust_custody_evidence,
    render_custody_census,
)

_REGISTRY = "backend/trust-authorities.json"
_SOURCES = {
    "admission_issuer_evidence": "backend/src/oh_goon/core/enforcer_lace_issuer_evidence.py",
    "control_plane_receipts": "backend/src/oh_goon/core/enforcer_lace_trust.py",
    "signer_trust_epoch": "backend/src/oh_goon/runtime/enforcer_lace_signer_trust_epoch.py",
}
_EXPECTED_VERIFIERS = {
    "TrustedIssuerVerifier": _SOURCES["admission_issuer_evidence"],
    "ReceiptAuthenticator": _SOURCES["control_plane_receipts"],
    "SignerTrustEpochAuthority": _SOURCES["signer_trust_epoch"],
}
_REQUIRED_VERIFICATION = {
    "admission_issuer_evidence": ("TrustedIssuerVerifier", "verify"),
    "control_plane_receipts": ("ReceiptAuthenticator", "verify"),
    "signer_trust_epoch": ("SignerTrustEpochAuthority", "require_in"),
}
_REQUIRED_CUSTODY_FILES = {
    "backend/src/oh_goon/runtime/enforcer_lace_issuer_keyring.py": "load_explicit_issuer_verifier",
    "backend/src/oh_goon/native_issuer/enforcer_lace_signer_custody.py": "issue_with_deployment_signer",
}


def _assignment(tree: ast.Module, key: str) -> object:
    assignments = [
        node.value
        for node in tree.body
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        and (
            key
            in (
                name.id
                for target in node.targets
                for name in [target]
                if isinstance(name, ast.Name)
            )
            if isinstance(node, ast.Assign)
            else isinstance(node.target, ast.Name) and node.target.id == key
        )
    ]
    if len(assignments) != 1:
        raise ValueError(f"source authority constant {key} missing or duplicated")
    expr = assignments[0]
    if (
        isinstance(expr, ast.Call)
        and isinstance(expr.func, ast.Name)
        and expr.func.id == "frozenset"
        and len(expr.args) == 1
        and not expr.keywords
    ):
        return frozenset(ast.literal_eval(expr.args[0]))
    return ast.literal_eval(expr)


def _source_tree(root: Path, source: str) -> ast.Module:
    return ast.parse((root / source).read_text(encoding="utf-8"), filename=source)


def _class_method(tree: ast.Module, cls: str, method: str) -> bool:
    return any(
        isinstance(node, ast.ClassDef)
        and node.name == cls
        and any(
            isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name == method
            for child in node.body
        )
        for node in tree.body
    )


def check_trust_authority_registry(root: Path = ROOT) -> list[Violation]:
    """Fail on source↔registry drift; never treat this design-only file as trust."""
    target = root / _REGISTRY
    issues: list[Violation] = []

    def violation(path: str, message: str) -> None:
        issues.append(Violation(root / path, f"T0A trust registry: {message}"))

    try:
        raw = target.read_bytes()
        policy = json.loads(raw)
        if (
            not isinstance(policy, dict)
            or policy.get("schema") != "oh-goon/trust-authority-registry/v1"
        ):
            raise ValueError("missing canonical schema")
        if json.dumps(policy, sort_keys=True, indent=2, ensure_ascii=False).encode() + b"\n" != raw:
            raise ValueError("registry must use canonical sorted JSON representation")
        if (
            policy.get("status") != "SOURCE_CENSUS_ONLY_NOT_RUNTIME_AUTHORITY"
            or policy.get("policy_provenance") != "NOT_ATTESTED"
            or policy.get("deployment_custody") != "NOT_OBSERVED"
            or policy.get("migration") != "NOT_STARTED_T1"
            or policy.get("gate_a") != "BLOCKED"
        ):
            raise ValueError("T0A cannot claim live trust policy, deployed custody or Gate A")
        if set(policy.get("families", {})) != set(_SOURCES):
            raise ValueError("unknown/missing authority family")
        issuer = policy["families"]["admission_issuer_evidence"]
        receipts = policy["families"]["control_plane_receipts"]
        epoch = policy["families"]["signer_trust_epoch"]
        for family, source in _SOURCES.items():
            if policy["families"][family].get("source") != source:
                violation(_REGISTRY, f"{family} tried to select alternate trust source")
        if (
            issuer.get("verifier") != "TrustedIssuerVerifier.verify"
            or issuer.get("root_loader")
            != "backend/src/oh_goon/runtime/enforcer_lace_issuer_keyring.py:load_explicit_issuer_verifier"
            or issuer.get("signer")
            != "backend/src/oh_goon/native_issuer/enforcer_lace_signer_custody.py:issue_with_deployment_signer"
            or receipts.get("signer_verifier") != "ReceiptAuthenticator.issue/verify"
            or epoch.get("verifier") != "SignerTrustEpochAuthority.require_in"
        ):
            violation(_REGISTRY, "canonical verifier/custody mapping was changed")
        if (
            issuer.get("algorithm") != "Ed25519"
            or receipts.get("algorithm") != "HMAC-SHA256"
            or policy.get("unimplemented_roles")
            != [
                "externally_provisioned_root_or_bootstrap_attestation",
                "independently_custodied_physical_observer_attestation",
            ]
        ):
            violation(
                _REGISTRY, "cryptographic mechanism or pending role changed without migration"
            )
        actual_claims = _assignment(_source_tree(root, issuer["source"]), "AUTHORITY_FIELDS")
        actual_domains = _assignment(_source_tree(root, receipts["source"]), "RECEIPT_DOMAINS")
        actual_protocol = _assignment(_source_tree(root, issuer["source"]), "ISSUER_PROTOCOL")
        actual_issuer_domain = _assignment(_source_tree(root, issuer["source"]), "SIGNATURE_DOMAIN")
        if set(issuer.get("claims", {})) != set(actual_claims) or any(
            state != "UNREVIEWED" for state in issuer["claims"].values()
        ):
            violation(
                _REGISTRY,
                "issuer claim census differs from live AUTHORITY_FIELDS or claims unreviewed custody",
            )
        if issuer.get("currently_required_distinct_key_count") != len(actual_claims):
            violation(_REGISTRY, "distinct signer/root count differs from source")
        if (
            issuer.get("independent_semantic_authorities_verified") is not False
            or issuer.get("custody_evidence") != "MISSING_NATIVE_GATE_A"
        ):
            violation(_REGISTRY, "unverified issuer custody cannot be promoted by T0A")
        if (
            receipts.get("receipt_domains") != sorted(actual_domains)
            or receipts.get("custody_evidence") != "MISSING_NATIVE_GATE_A"
        ):
            violation(_REGISTRY, "receipt domain census differs from live RECEIPT_DOMAINS")
        if issuer.get("protocol") != actual_protocol or issuer.get(
            "signature_domain"
        ) != actual_issuer_domain.decode("ascii").replace("\x00", "\\x00"):
            violation(_REGISTRY, "issuer protocol/signature domain drift")
        receipt_tree = _source_tree(root, receipts["source"])
        # ReceiptAuthenticator._mac has a fixed byte-literal domain, not a module constant.
        auth_class = next(
            (
                node
                for node in receipt_tree.body
                if isinstance(node, ast.ClassDef) and node.name == "ReceiptAuthenticator"
            ),
            None,
        )
        mac = (
            next(
                (
                    node
                    for node in auth_class.body
                    if isinstance(node, ast.FunctionDef) and node.name == "_mac"
                ),
                None,
            )
            if auth_class
            else None
        )
        mac_literals = (
            [
                node.value
                for node in ast.walk(mac)
                if isinstance(node, ast.Constant) and isinstance(node.value, bytes)
            ]
            if mac
            else []
        )
        if (
            receipts.get("signature_domain") != "oh-goon/receipt-hmac-sha256/v1\\x00"
            or b"oh-goon/receipt-hmac-sha256/v1\x00" not in mac_literals
        ):
            violation(_REGISTRY, "control-plane receipt domain differs from signer implementation")
        if epoch.get("separate_cryptographic_signer") is not False:
            violation(_REGISTRY, "SQLite trust epoch is not an independent signer")
        census_file = "docs/architecture/TRUST_AUTHORITY_CENSUS.md"
        issues.extend(check_trust_custody_evidence(root))
        if issues:
            return issues
        expected_census = _render_from_registry(policy, render_custody_census(root))
        if (root / census_file).read_text(encoding="utf-8") != expected_census:
            violation(census_file, "generated human census differs from authoritative registry")
        for family, (cls, method) in _REQUIRED_VERIFICATION.items():
            if not _class_method(_source_tree(root, _SOURCES[family]), cls, method):
                violation(_SOURCES[family], f"missing canonical {cls}.{method} verifier")
        for source, fn in _REQUIRED_CUSTODY_FILES.items():
            tree = _source_tree(root, source)
            if (
                sum(isinstance(node, ast.FunctionDef) and node.name == fn for node in tree.body)
                != 1
            ):
                violation(source, f"missing original {fn} custodian")
        # Do not claim that textual discovery proves there are no dynamic dispatch paths.
        # Still prevent simple duplicate verifier class declarations across the source tree.
        package = root / "backend/src/oh_goon"
        for path in package.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            relative = str(path.relative_to(root))
            if (
                relative.startswith("backend/src/oh_goon/runtime/")
                or relative.startswith("backend/src/oh_goon/services/")
                or relative.startswith("backend/src/oh_goon/integrations/")
            ):
                if "trust-custody-evidence.json" in path.read_text(encoding="utf-8"):
                    violation(
                        relative, "runtime trust decision must not consume custody review input"
                    )
            for node in tree.body:
                if isinstance(node, ast.ClassDef) and node.name in _EXPECTED_VERIFIERS:
                    expected = root / _EXPECTED_VERIFIERS[node.name]
                    if path != expected:
                        violation(
                            str(path.relative_to(root)), f"duplicate {node.name} authority verifier"
                        )
    except (OSError, ValueError, KeyError, TypeError, SyntaxError, AttributeError) as exc:
        violation(_REGISTRY, f"cannot validate registry against source: {exc}")
    return issues


def render_trust_census(root: Path = ROOT) -> str:
    """Human projection ONLY. A markdown census is never runtime policy."""
    issues = check_trust_authority_registry(root)
    if issues:
        raise ValueError(
            "trust census source/registry mismatch: " + "; ".join(i.message for i in issues)
        )
    payload = json.loads((root / _REGISTRY).read_text(encoding="utf-8"))
    return _render_from_registry(payload, render_custody_census(root))


def _render_from_registry(payload: dict, custody_section: str) -> str:
    a = payload["families"]["admission_issuer_evidence"]
    b = payload["families"]["control_plane_receipts"]
    rows = [
        "# Trust Authority Census — T0A source inventory",
        "",
        "**Status:** DESIGN-ONLY source inventory; native deployment custody NOT observed; Gate A BLOCKED.",
        "**Source of policy intent:** `backend/trust-authorities.json`; this document is a generated projection.",
        "**Existing runtime:** unchanged; T1 exclusive migration and authenticated bootstrap NOT implemented.",
        "",
        "## Observed cryptographic mechanisms",
        "",
        "| Family | Issuance / verification | Distinct keys required today | Independent custody proven? |",
        "|---|---|---:|---|",
        f"| Admission issuer evidence | Ed25519 / `{a['verifier']}` | {a['currently_required_distinct_key_count']} | NO |",
        f"| Control-plane receipts | HMAC-SHA256 / `{b['signer_verifier']}` | one operational key identity (deployment not observed) | NO |",
        "| Signer epoch | SQLite original admission transaction, not a new signer | 0 | Native Gate A pending |",
        "",
        "## Current field-specific issuer claims (not six proven semantic authorities)",
        "",
        "| Claim | Status | Custodian | Proposed disposition |",
        "|---|---|---|---|",
    ]
    for field, status in a["claims"].items():
        rows.append(
            f"| `{field}` | {status} | NOT_OBSERVED | UNDECIDED (KEEP/MERGE/EXTERNAL/REMOVE) |"
        )
    rows += [
        "",
        "## Control-plane signed receipt domains (shared HMAC signer)",
        "",
        ", ".join(f"`{d}`" for d in b["receipt_domains"]),
        "",
        "## Blocked external evidence before authority-minimal migration",
        "",
        "For EACH issuer/root: independently establish semantic owner, signing principal, public-root owner,",
        "OS UID/process custody, compromise boundary, benefit over domain separation, rotation/revocation",
        "and external provisioning. Source code cannot establish independent deployment custody.",
        "A public key file is not proof that its private signing key has independent custody.",
        "No key/root count can be reduced until T0B owner review and T1 single-path migration are complete.",
        "Never infer Gate A, physical Gate B or agent effect permission from this report.",
        "",
    ]
    return "\n".join(rows) + custody_section
