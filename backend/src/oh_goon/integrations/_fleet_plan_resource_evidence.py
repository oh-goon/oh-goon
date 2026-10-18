from __future__ import annotations

from pathlib import Path

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.fleet_plan_evidence import PlanResourceEvidence
from oh_goon.core.models.fleet_plan_isolation import CicdResourcePolicy
from oh_goon.integrations.exact_file_write import publish_text_file

_RESOURCE_EVIDENCE_FILE = "resource-evidence.json"


def read_attempt_resource_evidence(attempt_root: Path) -> PlanResourceEvidence | None:
    path = attempt_root / _RESOURCE_EVIDENCE_FILE
    if not path.is_file():
        return None
    try:
        return PlanResourceEvidence.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise FleetPlanInvalid(
            f"Fleet attempt resource evidence is unreadable or invalid: {path}"
        ) from exc


def write_remote_resource_evidence(
    attempt_root: Path,
    policy: CicdResourcePolicy,
) -> PlanResourceEvidence:
    """Record an externally enforced envelope when the remote backend exposes no peak API."""

    evidence = PlanResourceEvidence(
        cpu_millis=policy.cpu_millis,
        memory_bytes=policy.memory_bytes,
        max_processes=policy.max_processes,
    )
    write_attempt_resource_evidence(attempt_root, evidence)
    return evidence


def write_attempt_resource_evidence(
    attempt_root: Path,
    evidence: PlanResourceEvidence,
) -> None:
    path = attempt_root / _RESOURCE_EVIDENCE_FILE
    publish_text_file(path, evidence.model_dump_json(exclude_computed_fields=True))
