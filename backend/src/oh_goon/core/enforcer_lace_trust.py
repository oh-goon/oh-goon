"""M148: pure, non-live contract and authenticated receipt protocol.

The caller supplies trusted key material from the control-plane key authority.  No
key value is serialized into contract or receipt evidence.  A receipt signature
is not a substitute for the independent journal/containment proof.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Any, Mapping

PROTOCOL_VERSION = "enforcer-lace-authority/v1"
DIGEST_PREFIX = "sha256:"
# Stable diagnostic projections of the original FleetRunLifecycle admission
# denial. They do not authorize replay or provisioning, and allow the
# certification reporter to distinguish missing authority without redeciding it.
UNPROVISIONED_FLEET_PLAN_INGRESS = (
    "production Plan ingress Fleet Enforcer authority not provisioned"
)
UNPROVISIONED_FLEET_ADMISSION = "production Fleet Enforcer authority not provisioned"
CONTRACT_KEYS = frozenset({
    "run_id", "resolved_plan_identity", "repository_identity", "source_identity",
    "lock_identity", "native_tool_identity", "execution_environment_identity",
    "backend_capability_manifest_identity", "executor_generation", "run_authority",
    "step_envelopes", "step_teardown_envelopes", "run_teardown_envelope",
    "lineage_policy", "artifact_type_policy", "evidence_policy", "redaction_policy",
    "retention_policy", "observation_schema_version", "evaluator_version",
    "rollup_version", "journal_schema_version", "journal_identity_version",
    "receipt_canonicalization_version", "receipt_authentication_scheme_version",
})
RECEIPT_DOMAINS = frozenset({"admission", "step-output", "terminal", "unresolved", "recovery-transfer", "admission-signer-epoch", "agent-source-prework", "agent-source-prework-stage", "agent-tool-launch-grant"})


class TrustProtocolError(ValueError):
    """Contract or authenticated authority could not be proven."""


def _check_tree(value: Any) -> None:
    if value is None or type(value) in (str, int, bool):
        return
    if isinstance(value, dict):
        if not all(type(k) is str for k in value):
            raise TrustProtocolError("authority map keys must be strings")
        for child in value.values():
            _check_tree(child)
        return
    if isinstance(value, list):
        for child in value:
            _check_tree(child)
        return
    raise TrustProtocolError("unsupported or mutable/noncanonical authority value")


def canonical_bytes(payload: Mapping[str, Any]) -> bytes:
    """Deterministic UTF-8 JSON; reject floats, NaN, tuple and custom encoders."""
    if not isinstance(payload, dict):
        raise TrustProtocolError("authority must be a plain mapping")
    _check_tree(payload)
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(domain: str, payload: bytes) -> str:
    if not domain or not domain.isascii():
        raise TrustProtocolError("invalid digest domain")
    return DIGEST_PREFIX + hashlib.sha256(domain.encode("ascii") + b"\x00" + payload).hexdigest()


@dataclass(frozen=True, slots=True)
class AdmittedRunContract:
    """Closed and byte-frozen candidate: cannot authorize execution alone."""

    encoded: bytes
    identity: str

    @classmethod
    def freeze(cls, authority: dict[str, Any]) -> AdmittedRunContract:
        if set(authority) != CONTRACT_KEYS:
            raise TrustProtocolError("incomplete or unknown AdmittedRunContract authority")
        if not isinstance(authority["run_id"], str) or not authority["run_id"]:
            raise TrustProtocolError("run identity required")
        if type(authority["executor_generation"]) is not int or authority["executor_generation"] < 1:
            raise TrustProtocolError("positive executor generation required")
        for field in ("execution_environment_identity", "backend_capability_manifest_identity"):
            value = authority[field]
            if not isinstance(value, str) or not value.startswith(DIGEST_PREFIX) or len(value) != 71:
                raise TrustProtocolError(f"exact {field} digest required")
            if any(c not in "0123456789abcdef" for c in value[7:]):
                raise TrustProtocolError(f"noncanonical {field} digest")
        for field in ("run_authority", "step_envelopes", "step_teardown_envelopes",
                      "run_teardown_envelope"):
            if not isinstance(authority[field], dict) or not authority[field]:
                raise TrustProtocolError(f"structured {field} authority required")
        for field in CONTRACT_KEYS - {"executor_generation"}:
            if authority[field] is None or authority[field] == "" or authority[field] == {}:
                raise TrustProtocolError(f"empty {field} authority")
        encoded = canonical_bytes(authority)
        return cls(encoded, digest("oh-goon/admitted-run-contract/v1", encoded))

    @property
    def fields(self) -> dict[str, Any]:
        return json.loads(self.encoded)

    def verify(self) -> None:
        if AdmittedRunContract.freeze(self.fields) != self:
            raise TrustProtocolError("noncanonical or forged frozen Run contract")


@dataclass(frozen=True, slots=True)
class AuthenticatedReceipt:
    domain: str
    key_id: str
    payload: bytes
    mac_hex: str

    def fields(self) -> dict[str, Any]:
        return json.loads(self.payload)


class ReceiptAuthenticator:
    """Versioned control-plane HMAC issuer/verifier; no ambient key fallback."""

    def __init__(self, *, key_id: str, secret: bytes) -> None:
        if not key_id or not key_id.isascii() or any(c in key_id for c in "\x00\n/"):
            raise TrustProtocolError("invalid key identity")
        if not isinstance(secret, bytes) or len(secret) < 32:
            raise TrustProtocolError("independently provided 256-bit key required")
        self.key_id = key_id
        self._secret = secret

    def _mac(self, domain: str, payload: bytes) -> str:
        message = b"oh-goon/receipt-hmac-sha256/v1\x00" + domain.encode("ascii") + b"\x00" + payload
        return hmac.new(self._secret, message, hashlib.sha256).hexdigest()

    def issue(self, domain: str, payload: dict[str, Any]) -> AuthenticatedReceipt:
        if domain not in RECEIPT_DOMAINS:
            raise TrustProtocolError("unknown receipt domain")
        data = canonical_bytes(payload)
        return AuthenticatedReceipt(domain, self.key_id, data, self._mac(domain, data))

    def verify(self, receipt: AuthenticatedReceipt, *, domain: str) -> dict[str, Any]:
        if domain not in RECEIPT_DOMAINS or receipt.domain != domain or receipt.key_id != self.key_id:
            raise TrustProtocolError("wrong receipt domain or trusted key identity")
        try:
            decoded = json.loads(receipt.payload)
        except (ValueError, UnicodeDecodeError) as exc:
            raise TrustProtocolError("receipt has invalid encoding") from exc
        if canonical_bytes(decoded) != receipt.payload:
            raise TrustProtocolError("receipt encoding is noncanonical")
        if not hmac.compare_digest(self._mac(domain, receipt.payload), receipt.mac_hex):
            raise TrustProtocolError("receipt authentication failed")
        return decoded


def admission_receipt(auth: ReceiptAuthenticator, contract: AdmittedRunContract) -> AuthenticatedReceipt:
    return auth.issue("admission", {"decision": "ADMITTED", "run_id": contract.fields["run_id"],
                                   "contract_identity": contract.identity, "protocol": PROTOCOL_VERSION})


def rejected_receipt(auth: ReceiptAuthenticator, *, run_id: str, reason: str) -> AuthenticatedReceipt:
    if not run_id or not reason:
        raise TrustProtocolError("rejection requires run and reason")
    return auth.issue("admission", {"decision": "REJECTED", "run_id": run_id,
                                   "reason": reason, "protocol": PROTOCOL_VERSION})


def step_output_receipt(auth: ReceiptAuthenticator, *, run_id: str, step_id: str,
                        contract_identity: str, output_identity: str, semantic_type: str,
                        lineage_identity: str) -> AuthenticatedReceipt:
    if not all((run_id, step_id, contract_identity, output_identity, semantic_type, lineage_identity)):
        raise TrustProtocolError("complete Step output lineage required")
    return auth.issue("step-output", {"protocol": PROTOCOL_VERSION, "run_id": run_id,
        "step_id": step_id, "contract_identity": contract_identity,
        "output_identity": output_identity, "semantic_type": semantic_type,
        "lineage_identity": lineage_identity})
