"""T0B design-time evidence intake contract, NOT custody attestation or runtime trust.

The source-backed registry remains the only authority *inventory*. This file is a
pending-evidence register that cannot grant trust, authorize Runs or pass Gate A.
Real custody requires independently issued, verified deployment evidence in T0B2.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from oh_goon.repository_tooling.architecture.common import Violation
from oh_goon.repository_tooling.architecture.repository_layout import ROOT

_CUSTODY = "backend/trust-custody-evidence.json"
_REGISTRY = "backend/trust-authorities.json"
_REVIEW_FIELDS = (
    "semantic_owner",
    "private_key_custodian",
    "public_root_custodian",
    "os_process_principal",
    "compromise_boundary",
    "independent_security_benefit",
    "domain_separation_insufficiency",
    "rotation",
    "revocation",
    "provisioning",
)
_PENDING_ROLES = (
    "provisioning_root",
    "independent_physical_observer",
)


def expected_subjects(policy: dict[str, Any]) -> tuple[str, ...]:
    """Derive the complete review scope from the one source-backed registry."""
    claims = policy["families"]["admission_issuer_evidence"]["claims"]
    return tuple(
        sorted(
            [f"admission_issuer_evidence/{field}" for field in claims]
            + ["control_plane_receipts", "signer_trust_epoch"]
            + [f"future_role/{role}" for role in _PENDING_ROLES]
        )
    )


def _canonical(payload: Mapping[str, object]) -> bytes:
    return (json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()


def check_trust_custody_evidence(root: Path = ROOT) -> list[Violation]:
    """Fail closed on changed trust scope or unauthorized custody promotion."""
    target = root / _CUSTODY
    try:
        registry_bytes = (root / _REGISTRY).read_bytes()
        policy = json.loads(registry_bytes)
        raw = target.read_bytes()
        evidence = json.loads(raw)
        if not isinstance(evidence, dict) or raw != _canonical(evidence):
            raise ValueError("custody register is not canonical JSON")
        if set(evidence) != {
            "schema",
            "policy_sha256",
            "status",
            "gate_a",
            "runtime_authority",
            "subjects",
        }:
            raise ValueError("custody register top-level fields missing or unreviewed fields added")
        if evidence["schema"] != "oh-goon/trust-custody-evidence/v1":
            raise ValueError("unknown custody evidence schema")
        if evidence["policy_sha256"] != hashlib.sha256(registry_bytes).hexdigest():
            raise ValueError("custody register not bound to exact authority registry")
        if (
            evidence["status"] != "PENDING_INDEPENDENT_REVIEW"
            or evidence["gate_a"] != "BLOCKED"
            or evidence["runtime_authority"] is not False
        ):
            raise ValueError("design-only custody cannot grant trust or qualify Gate A")
        subjects = evidence["subjects"]
        if not isinstance(subjects, dict) or set(subjects) != set(expected_subjects(policy)):
            raise ValueError(
                "custody subjects must equal all registered claims and pending trust roles"
            )
        for subject, review in subjects.items():
            if not isinstance(review, dict) or set(review) != {
                "review_state",
                "disposition",
                "evidence_refs",
                "observations",
            }:
                raise ValueError(f"{subject}: review record missing fields or additional authority")
            if (
                review["review_state"] != "UNREVIEWED"
                or review["disposition"] != "UNDECIDED"
                or review["evidence_refs"] != []
            ):
                raise ValueError(f"{subject}: unverified external facts cannot be promoted")
            observations = review["observations"]
            if not isinstance(observations, dict) or set(observations) != set(_REVIEW_FIELDS):
                raise ValueError(f"{subject}: required custodian and compromise fields missing")
            if any(observations[field] != "NOT_OBSERVED" for field in _REVIEW_FIELDS):
                raise ValueError(
                    f"{subject}: source cannot attest real-world custodian information"
                )
        # The input cannot be an admission, signature or runtime-policy file: the
        # only consumer is this read-only architecture check and census projection.
        return []
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        return [Violation(target, f"T0B custody review is incomplete or unauthenticated: {exc}")]


def render_custody_census(root: Path = ROOT) -> str:
    """Append to the ONE generated Trust Authority Census, never create a rival census."""
    issues = check_trust_custody_evidence(root)
    if issues:
        raise ValueError("; ".join(issue.message for issue in issues))
    evidence = json.loads((root / _CUSTODY).read_text(encoding="utf-8"))
    rows = [
        "## T0B — deployment custody and authority review",
        "",
        "**Review state:** PENDING independent deployment evidence; Gate A BLOCKED.",
        "This table is projected from `backend/trust-custody-evidence.json` and bound by SHA-256",
        "to the only trust authority registry (`backend/trust-authorities.json`).",
        "The evidence register is NOT an authenticated runtime policy, authority or signer.",
        "",
        "| Exact subject | Custody | Disposition | Independent evidence |",
        "|---|---|---|---|",
    ]
    for name, review in sorted(evidence["subjects"].items()):
        rows.append(
            f"| `{name}` | {review['review_state']} | {review['disposition']} | NOT PROVIDED |"
        )
    rows += [
        "",
        "Independent review must establish every semantic owner, private/public-key custodian, OS",
        "principal, compromise boundary, security benefit over domain separation, rotation,",
        "revocation and provisioning before T1 can change any original trust requirement.",
        "No key material or secrets belong in this repository or review register.",
        "A source-file assertion, code signature, key path or owner name is NOT physical custody proof.",
        "A later checkpoint must authenticate externally collected evidence and explicitly approve",
        "the authority migration; this checkpoint intentionally cannot mark any subject VERIFIED.",
        "",
    ]
    return "\n".join(rows)
