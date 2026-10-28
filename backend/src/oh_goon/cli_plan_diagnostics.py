"""CLI presentation of canonical redacted Plan-owner diagnostics."""

from __future__ import annotations

import json

from oh_goon.core.plan_validation_diagnostics import safe_plan_diagnostic


def print_safe_plan_diagnostic(exc: Exception, *, as_json: bool) -> None:
    report = safe_plan_diagnostic(exc)
    if as_json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return
    print(f"Plan rejected: {report['code']}")
    if report["inputs"]:
        print(f"Inputs: {', '.join(report['inputs'])}")
    if report.get("fields"):
        print(f"Fields: {', '.join(report['fields'])}")
    print(f"Next action: {report['safe_next_action']}")
    print("No execution was initiated by this diagnostic presentation.")
