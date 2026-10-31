"""CLI presentation of canonical redacted Plan-owner diagnostics."""

from __future__ import annotations

import json

from oh_goon.core.plan_validation_diagnostics import safe_plan_diagnostic


def print_safe_plan_diagnostic(
    exc: Exception, *, as_json: bool, target_registration: dict[str, str] | None = None
) -> None:
    report = safe_plan_diagnostic(exc)
    if as_json:
        registration = (
            {"target_registration": target_registration} if target_registration is not None else {}
        )
        print(json.dumps({**report, **registration}, indent=2, ensure_ascii=False))
        return
    print(f"Plan rejected: {report['code']}")
    if report["inputs"]:
        print(f"Inputs: {', '.join(report['inputs'])}")
    if report.get("fields"):
        print(f"Fields: {', '.join(report['fields'])}")
    print(f"Next action: {report['safe_next_action']}")
    if target_registration is not None:
        print(
            f"Target registration: {target_registration['status']} ({target_registration['path']})"
        )
    print("No execution was initiated by this diagnostic presentation.")
