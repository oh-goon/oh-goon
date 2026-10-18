"""M151/M154: separately issued, verification-only admission claims.

This pure verifier does not collect evidence, prove native systems, issue leases,
select backends, or enable execution.  Each signing key must be supplied by the
*actual* owner of its authority; the Enforcer consumes, but never invents,
provenance. Enforcer may hold the public verification keys, never the private
signing keys. The live owner must also supply a trusted current-lease verifier.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from oh_goon.core.enforcer_lace_trust import (
    AdmittedRunContract, TrustProtocolError, canonical_bytes,
)

ISSUER_PROTOCOL = "oh-goon/admission-issuer-evidence/v2"
SIGNATURE_DOMAIN = b"oh-goon/admission-issuer-ed25519/v2\x00"
AUTHORITY_FIELDS = frozenset({
    "repository_identity", "source_identity", "lock_identity",
    "native_tool_identity", "execution_environment_identity",
    "backend_capability_manifest_identity",
})


@dataclass(frozen=True, slots=True)
class IssuerEvidence:
    """Authenticated claim about one contract field (never a self-asserted identity)."""

    field: str
    issuer_id: str
    payload: bytes
    signature_hex: str


class TrustedIssuerVerifier:
    """Consume distinct public trust roots; has no signing capability or defaults."""

    def __init__(self, roots: Mapping[str, tuple[str, bytes]]) -> None:
        if set(roots) != AUTHORITY_FIELDS:
            raise TrustProtocolError("all independent admission issuer roots required")
        frozen: dict[str, tuple[str, bytes]] = {}
        for field, root in roots.items():
            if not isinstance(root, tuple) or len(root) != 2:
                raise TrustProtocolError("invalid issuer root")
            issuer_id, public_key = root
            if (not isinstance(issuer_id, str) or not issuer_id.isascii()
                    or not issuer_id or any(c in issuer_id for c in "\x00\n/")):
                raise TrustProtocolError("invalid issuer identity")
            if type(public_key) is not bytes or len(public_key) != 32:
                raise TrustProtocolError("explicit 256-bit Ed25519 public key required")
            try:
                Ed25519PublicKey.from_public_bytes(public_key)
            except ValueError as exc:
                raise TrustProtocolError("invalid Ed25519 issuer public key") from exc
            frozen[field] = (issuer_id, public_key)
        if len({v[0] for v in frozen.values()}) != len(frozen):
            raise TrustProtocolError("issuer identities cannot be shared across authorities")
        if len({v[1] for v in frozen.values()}) != len(frozen):
            raise TrustProtocolError("issuer public keys cannot be shared across authorities")
        self._roots = frozen

    def public_roots(self) -> dict[str, tuple[str, bytes]]:
        """Copy only the six validated public roots; never expose signing material."""
        return dict(self._roots)

    def verify(
        self, contract: AdmittedRunContract, *, evidence: Sequence[IssuerEvidence],
        control_epoch: int, lease_identity: str,
        lease_is_current: Callable[[str, int, str], bool],
    ) -> None:
        """Prove every admitted identity plus independently queried current lease.

        The lease callback is a *trusted issuer port*, never a user callback or
        replacement for durable lease fencing in the transaction itself.
        """
        contract.verify()
        if type(control_epoch) is not int or control_epoch < 1 or not lease_identity:
            raise TrustProtocolError("positive control epoch and exact lease identity required")
        if not callable(lease_is_current):
            raise TrustProtocolError("trusted live lease owner required")
        if not isinstance(evidence, (tuple, list)) or len(evidence) != len(AUTHORITY_FIELDS):
            raise TrustProtocolError("exactly one issuer proof per authority required")
        fields = contract.fields
        seen: set[str] = set()
        for proof in evidence:
            if not isinstance(proof, IssuerEvidence) or proof.field not in AUTHORITY_FIELDS:
                raise TrustProtocolError("unknown or untrusted issuer proof")
            if proof.field in seen:
                raise TrustProtocolError("duplicate issuer evidence")
            seen.add(proof.field)
            issuer_id, public_key = self._roots[proof.field]
            if proof.issuer_id != issuer_id or type(proof.payload) is not bytes:
                raise TrustProtocolError("incorrect issuer or noncanonical evidence")
            try:
                payload = json.loads(proof.payload)
            except (ValueError, UnicodeDecodeError, TypeError) as exc:
                raise TrustProtocolError("invalid issuer payload") from exc
            expected = {
                "protocol": ISSUER_PROTOCOL, "field": proof.field,
                "run_id": fields["run_id"], "contract_identity": contract.identity,
                "identity": fields[proof.field],
                "executor_generation": fields["executor_generation"],
            }
            if payload != expected or canonical_bytes(payload) != proof.payload:
                raise TrustProtocolError("issuer proof does not bind exact admitted authority")
            message = SIGNATURE_DOMAIN + proof.field.encode("ascii") + b"\x00" + proof.payload
            try:
                if type(proof.signature_hex) is not str or len(proof.signature_hex) != 128:
                    raise TrustProtocolError("invalid issuer signature encoding")
                if proof.signature_hex != proof.signature_hex.lower():
                    raise TrustProtocolError("noncanonical issuer signature encoding")
                signature = bytes.fromhex(proof.signature_hex)
                Ed25519PublicKey.from_public_bytes(public_key).verify(signature, message)
            except (InvalidSignature, ValueError) as exc:
                raise TrustProtocolError("issuer authentication failed") from exc
        if seen != AUTHORITY_FIELDS:
            raise TrustProtocolError("admission issuer coverage incomplete")
        # This must be the trusted lease authority's *current* decision, not a
        # timestamp supplied in a receipt, a policy default, or a stale cache.
        if lease_is_current(fields["run_id"], control_epoch, lease_identity) is not True:
            raise TrustProtocolError("control lease cannot be proven current")
