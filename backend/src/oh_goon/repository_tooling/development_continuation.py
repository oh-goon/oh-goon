from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

CONTINUATION_INPUT_SCHEMA = "oh-goon-development-continuation-input-v1"
CONTINUATION_SCHEMA = "oh-goon-development-continuation-v1"


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode()


def _json_object(content: bytes, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid recovery {label} JSON") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"recovery {label} must be a JSON object")
    return payload


def _require_list(payload: dict[str, Any], field: str, *, label: str) -> None:
    if not isinstance(payload.get(field), list):
        raise RuntimeError(f"recovery {label} {field} must be a list")


def _open_phase_ids(open_phases: list[Any]) -> set[str]:
    phase_ids: set[str] = set()
    for item in open_phases:
        if isinstance(item, str):
            phase_ids.add(item)
        elif isinstance(item, dict) and isinstance(item.get("id"), str):
            phase_ids.add(item["id"])
    return phase_ids


def _validate_next_phase(payload: dict[str, Any]) -> None:
    open_phases = payload["open_phases"]
    next_phase = payload.get("next_executable_phase")
    if not open_phases:
        if next_phase is not None:
            raise RuntimeError(
                "recovery continuation cannot select a next phase when no phases are open"
            )
        return
    if not isinstance(next_phase, str) or not next_phase.strip():
        raise RuntimeError("recovery continuation must select one next executable phase")
    if next_phase not in _open_phase_ids(open_phases):
        raise RuntimeError("recovery continuation next phase is not present in open phases")


def continuation_input_from_bytes(
    content: bytes, *, qualification_state: str,
) -> dict[str, Any]:
    payload = _json_object(content, label="continuation input")
    if payload.get("schema") != CONTINUATION_INPUT_SCHEMA:
        raise RuntimeError("unsupported recovery continuation input schema")
    for field in ("completed_phases", "open_phases", "proof_receipts"):
        _require_list(payload, field, label="continuation input")
    if payload.get("qualification_state") != qualification_state:
        raise RuntimeError("recovery continuation qualification state mismatch")
    _validate_next_phase(payload)
    return payload


def continuation_input_sha256(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_json_bytes(payload)).hexdigest()


def continuation_state_bytes(
    *,
    root: Path,
    input_payload: dict[str, Any],
    campaign_id: str,
    generation: int,
    parent_source_identity: str | None,
    result_source_identity: str,
    archive_sha256: str,
    archive_size: int,
    object_key: str,
    proof_receipt_sha256: str | None,
    proof_level: str,
) -> bytes:
    version_path = root / "VERSION"
    if not version_path.is_file():
        raise RuntimeError("recovery source VERSION is missing")
    version = version_path.read_text(encoding="utf-8").strip()
    if not version:
        raise RuntimeError("recovery source VERSION is empty")
    payload: dict[str, Any] = {
        "schema": CONTINUATION_SCHEMA,
        "current_authority": {
            "version": version,
            "campaign_id": campaign_id,
            "generation": generation,
            "parent_source_identity": parent_source_identity,
            "source_identity": result_source_identity,
            "archive_sha256": archive_sha256,
            "archive_size": archive_size,
            "object_key": object_key,
            "recovery_proof_receipt_sha256": proof_receipt_sha256,
            "proof_level": proof_level,
        },
        "completed_phases": input_payload["completed_phases"],
        "open_phases": input_payload["open_phases"],
        "proof_receipts": input_payload["proof_receipts"],
        "qualification_state": input_payload["qualification_state"],
        "next_executable_phase": input_payload.get("next_executable_phase"),
    }
    if "durability_invariant" in input_payload:
        payload["durability_invariant"] = input_payload["durability_invariant"]
    return _json_bytes(payload)


def continuation_state_from_bytes(content: bytes) -> dict[str, Any]:
    payload = _json_object(content, label="continuation state")
    if payload.get("schema") != CONTINUATION_SCHEMA:
        raise RuntimeError("unsupported recovery continuation state schema")
    if not isinstance(payload.get("current_authority"), dict):
        raise RuntimeError("recovery continuation current authority is missing")
    for field in ("completed_phases", "open_phases", "proof_receipts"):
        _require_list(payload, field, label="continuation state")
    return payload


def render_handoff(status_payload: dict[str, Any]) -> str:
    head = status_payload["head"]
    continuation = status_payload["continuation"]
    authority = continuation["current_authority"]
    return "\n".join(
        [
            "# Oh-Goon durable continuation projection",
            "",
            "Generated from verified committed recovery state. This Markdown is not authority;",
            "`RECOVERY_HEAD.json` plus its digest-bound receipt, archive, proof, and continuation objects are authority.",
            "",
            "## Recovery HEAD",
            "",
            f"- Campaign: `{head['campaign_id']}`",
            f"- Generation: `{head['generation']}`",
            f"- Source identity: `{head['source_identity']}`",
            f"- Publication receipt SHA-256: `{head['checkpoint_receipt_sha256']}`",
            f"- Continuation state SHA-256: `{head['continuation_state_sha256']}`",
            f"- Qualification state: `{head['qualification_state']}`",
            f"- Proof level: `{head['proof_level']}`",
            "",
            "## Current authority",
            "",
            "```json",
            json.dumps(authority, sort_keys=True, indent=2),
            "```",
            "",
            "## Completed phases",
            "",
            "```json",
            json.dumps(continuation["completed_phases"], sort_keys=True, indent=2),
            "```",
            "",
            "## Open phases",
            "",
            "```json",
            json.dumps(continuation["open_phases"], sort_keys=True, indent=2),
            "```",
            "",
            "## Proof receipts",
            "",
            "```json",
            json.dumps(continuation["proof_receipts"], sort_keys=True, indent=2),
            "```",
            "",
            "## Next executable phase",
            "",
            f"`{continuation['next_executable_phase']}`",
            "",
        ]
    )
