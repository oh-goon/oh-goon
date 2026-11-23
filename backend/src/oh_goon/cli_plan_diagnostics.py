"""CLI presentation of canonical redacted Plan-owner diagnostics."""

from __future__ import annotations

import json
import sys

from oh_goon.core.models.agent_providers import PiSessionIntent
from oh_goon.core.plan_validation_diagnostics import safe_plan_diagnostic


def pi_prior_run_readback(session: PiSessionIntent | None) -> dict[str, object]:
    if session is None or session.prior_run_id is None:
        return {}
    return {
        "prior_run_id": session.prior_run_id,
        "prior_run_handoff_argv": ["oh-goon", "runs", "handoff", session.prior_run_id, "--json"],
    }


def print_safe_plan_diagnostic(
    exc: Exception,
    *,
    as_json: bool,
    target_registration: dict[str, str] | None = None,
    pi_session: PiSessionIntent | None = None,
) -> None:
    report = safe_plan_diagnostic(exc)
    pi_refusal = report["stage"] == "pi_session"
    prior_readback = pi_prior_run_readback(pi_session) if pi_refusal else {}
    if as_json:
        registration = (
            {"target_registration": target_registration} if target_registration is not None else {}
        )
        print(
            json.dumps({**report, **registration, **prior_readback}, indent=2, ensure_ascii=False)
        )
        return
    destination = sys.stderr if pi_refusal else sys.stdout
    if report["stage"] == "run_resources":
        label = "Run resources unavailable"
    elif report["code"] == "EXECUTION_BUSY":
        label = "Execution busy"
    else:
        label = "Plan rejected"
    print(f"{label}: {report['code']}", file=destination)
    if report["inputs"]:
        print(f"Inputs: {', '.join(report['inputs'])}", file=destination)
    if report.get("fields"):
        print(f"Fields: {', '.join(report['fields'])}", file=destination)
    print(f"Next action: {report['safe_next_action']}", file=destination)
    if prior_readback:
        print(
            "Prior Run handoff argv: " + json.dumps(prior_readback["prior_run_handoff_argv"]),
            file=destination,
        )
    if target_registration is not None:
        print(
            f"Target registration: {target_registration['status']} ({target_registration['path']})",
            file=destination,
        )
    print("No execution was initiated by this diagnostic presentation.", file=destination)
