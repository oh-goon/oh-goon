from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from oh_goon.core.models.fleet_plan_evidence import (
        PlanEgressEvidence,
        PlanExecutionInputEvidence,
        PlanResourceEvidence,
        RequirementsRepositoryAdmissionEvidence,
    )


def repository_admission_payload(
    evidence: RequirementsRepositoryAdmissionEvidence,
) -> dict[str, object]:
    return evidence.model_dump(mode="json", exclude={"validation_id"}, exclude_computed_fields=True)


def execution_input_payload(evidence: PlanExecutionInputEvidence) -> dict[str, object]:
    return {
        "repository_id": evidence.repository_id,
        "source_authority_id": evidence.source_authority_id,
        "base_workspace": evidence.base_workspace.model_dump(
            mode="json", exclude_computed_fields=True
        ),
        "repository_admission": (
            evidence.repository_admission.model_dump(mode="json", exclude_computed_fields=True)
            if evidence.repository_admission is not None
            else None
        ),
        "import_applicability": [
            item.model_dump(mode="json", exclude_computed_fields=True)
            for item in evidence.import_applicability
        ],
        "runtime_root": evidence.runtime_root.model_dump(mode="json", exclude_computed_fields=True),
        "isolation_backend": evidence.isolation_backend.model_dump(
            mode="json", exclude_computed_fields=True
        ),
        "host_environment_names": evidence.host_environment_names,
        "host_tools": [
            item.model_dump(mode="json", exclude_computed_fields=True)
            for item in evidence.host_tools
        ],
        "managed_inputs": [
            item.model_dump(mode="json", exclude_computed_fields=True)
            for item in evidence.managed_inputs
        ],
        "content_inputs": [
            item.model_dump(mode="json", exclude_computed_fields=True)
            for item in evidence.content_inputs
        ],
        "hostname_resolutions": [
            item.model_dump(mode="json", exclude_computed_fields=True)
            for item in evidence.hostname_resolutions
        ],
    }


def egress_payload(evidence: PlanEgressEvidence) -> dict[str, object]:
    return {
        "mode": evidence.mode,
        "rules": [
            rule.model_dump(mode="json", exclude_computed_fields=True) for rule in evidence.rules
        ],
    }


def resource_policy_payload(evidence: PlanResourceEvidence) -> dict[str, object]:
    return {
        "cpu_millis": evidence.cpu_millis,
        "memory_bytes": evidence.memory_bytes,
        "max_processes": evidence.max_processes,
    }
