from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Literal

OperationOutcome = Literal["PERFORMED", "REUSED", "FAILED"]


@dataclass(frozen=True, slots=True)
class OperationEconomicsReceipt:
    """Passive owner-level I/O economics; never execution or identity authority."""

    schema: int
    operation_identity: str
    operation: str
    authority_owner: str
    artifact_identity: str
    outcome: OperationOutcome
    reason: str
    bytes_hashed: int
    bytes_read: int
    bytes_written: int
    bytes_copied: int
    reused_bytes: int
    strong_proof_performed: bool
    strong_proof_reused: bool
    elapsed_seconds: float

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def operation_identity(*, operation: str, authority_owner: str, artifact_identity: str) -> str:
    payload = json.dumps(
        {
            "schema": 1,
            "operation": operation,
            "authority_owner": authority_owner,
            "artifact_identity": artifact_identity,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def economics_receipt(
    *,
    operation: str,
    authority_owner: str,
    artifact_identity: str,
    outcome: OperationOutcome,
    reason: str,
    bytes_hashed: int = 0,
    bytes_read: int = 0,
    bytes_written: int = 0,
    bytes_copied: int = 0,
    reused_bytes: int = 0,
    strong_proof_performed: bool = False,
    strong_proof_reused: bool = False,
    elapsed_seconds: float,
) -> OperationEconomicsReceipt:
    return OperationEconomicsReceipt(
        schema=1,
        operation_identity=operation_identity(
            operation=operation,
            authority_owner=authority_owner,
            artifact_identity=artifact_identity,
        ),
        operation=operation,
        authority_owner=authority_owner,
        artifact_identity=artifact_identity,
        outcome=outcome,
        reason=reason,
        bytes_hashed=bytes_hashed,
        bytes_read=bytes_read,
        bytes_written=bytes_written,
        bytes_copied=bytes_copied,
        reused_bytes=reused_bytes,
        strong_proof_performed=strong_proof_performed,
        strong_proof_reused=strong_proof_reused,
        elapsed_seconds=elapsed_seconds,
    )
