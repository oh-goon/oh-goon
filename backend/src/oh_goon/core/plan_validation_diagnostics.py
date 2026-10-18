"""Transport-neutral and redacted presentation of typed Plan-owner rejections.

No source access, reclassification or execution is permitted here.
"""

from __future__ import annotations

import re

from oh_goon.core.errors import FleetPlanInputRejected

_SCHEMA = "oh-goon.plan-validation-diagnostic/v1"
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_-]{0,63}\Z")
_NEXT_ACTIONS = {
    "UNKNOWN_INPUT": "Use plan inputs to inspect accepted input names; remove unrecognized caller inputs.",
    "MISSING_REQUIRED_INPUT": "Supply required inputs explicitly with --input or --inputs-file.",
    "INVALID_INPUT_VALUE": "Use plan inputs to inspect the owning Goon's type and constraints; correct the caller value.",
    "INVALID_INPUT_DEFAULT": "Correct the default in the owning Goon input definition.",
    "CONFLICTING_INPUT_SOURCES": "Supply each input from exactly one of --input or --inputs-file.",
    "UNOWNED_PROJECTED_INPUT": "Expose the input through one selected Goon or author an explicit binding.",
    "AMBIGUOUS_PROJECTED_INPUT": "Choose one Composite owner or bind the input explicitly; no implicit fan-out.",
}


def safe_plan_diagnostic(exc: Exception) -> dict[str, object]:
    """Project a *single* received rejection without a second semantic decision."""
    if isinstance(exc, FleetPlanInputRejected) and exc.diagnostic_code in _NEXT_ACTIONS:
        code = exc.diagnostic_code
        inputs = sorted({name for name in exc.input_names if _IDENTIFIER.fullmatch(name)})
        stage = "input_contract"
        action = _NEXT_ACTIONS[code]
    else:
        code = "PLAN_RESOLUTION_REJECTED"
        inputs = []
        stage = "plan_resolution"
        action = "Review the Plan/Goon source and caller input contract, then retry validation."
    return {
        "schema": _SCHEMA,
        "status": "rejected",
        "stage": stage,
        "code": code,
        "inputs": inputs,
        "safe_next_action": action,
        "execution": "not_started_by_diagnostic",
    }
