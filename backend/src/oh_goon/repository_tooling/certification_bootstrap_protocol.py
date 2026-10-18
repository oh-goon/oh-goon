from __future__ import annotations

import json
from collections.abc import Mapping

from oh_goon.core.enforcer_lace_trust import (
    UNPROVISIONED_FLEET_ADMISSION,
    UNPROVISIONED_FLEET_PLAN_INGRESS,
)


SUCCESS_RECORD_ID = "OH_GOON_CERTIFICATION_BOOTSTRAP"
ERROR_RECORD_ID = "OH_GOON_CERTIFICATION_BOOTSTRAP_ERROR"
FLEET_PROVISIONING_BLOCKER_RECORD_ID = "OH_GOON_FLEET_PROVISIONING_BLOCKER"


def is_missing_fleet_provisioning(output: str) -> bool:
    """Classify lifecycle's original denial for reporting only, never admission."""
    # Only the original lifecycle's complete standalone denial is evidence.
    # An unrelated exception, diagnostic quotation or attacker-controlled
    # string containing the phrase must not reclassify APP certification.
    return any(
        line.strip() in (
            UNPROVISIONED_FLEET_PLAN_INGRESS,
            UNPROVISIONED_FLEET_ADMISSION,
        )
        for line in output.splitlines()
    )


def _render_record(record_id: str, payload: Mapping[str, object]) -> str:
    return f"{record_id}={json.dumps(dict(payload), sort_keys=True)}"


def render_success_record(payload: Mapping[str, object]) -> str:
    return _render_record(SUCCESS_RECORD_ID, payload)


def render_error_record(payload: Mapping[str, object]) -> str:
    return _render_record(ERROR_RECORD_ID, payload)


def render_missing_fleet_provisioning_blocker() -> str:
    """Report original admission denial, never mint or infer authority."""
    return _render_record(FLEET_PROVISIONING_BLOCKER_RECORD_ID, {
        "schema": "oh-goon.fleet-provisioning-blocker/v1",
        "reason": "FLEET_ENFORCER_NOT_PROVISIONED",
        "status": "BLOCKED",
        "authority": "NOT_ESTABLISHED",
        "certification_goon_started": False,
        "app_pass": False,
        "authenticated_replay": "NOT_AUTHORIZED",
        "group_a_gate_a": "NOT_RUN",
    })


def _payloads(output: str, record_id: str) -> list[str]:
    prefix = record_id + "="
    return [line[len(prefix) :] for line in output.splitlines() if line.startswith(prefix)]


def success_payloads(output: str) -> list[str]:
    return _payloads(output, SUCCESS_RECORD_ID)


def error_payloads(output: str) -> list[str]:
    return _payloads(output, ERROR_RECORD_ID)