"""Transport-neutral and redacted presentation of typed Plan-owner rejections.

No source access, reclassification or execution is permitted here.
"""

from __future__ import annotations

import re
from typing import NotRequired, TypedDict

from oh_goon.core.errors import FleetPlanInputRejected, RunConfigurationInvalid

_SCHEMA = "oh-goon.plan-validation-diagnostic/v1"
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_-]{0,63}\Z")
_CONFIG_ACTIONS = {
    "CONFIG_SCHEMA_INVALID": "Correct the named run.yml fields using the version 1 selection contract.",
    "CONFIG_PATH_INVALID": "Select an existing regular source contained in the workflow root; remove traversal and symlink aliases.",
    "CONFIG_TARGET_UNAVAILABLE": "Set target.repository_id to exactly one included registered repository.",
    "CONFIG_CHANGED": "The captured configuration changed; inspect the current run.yml and explicitly regenerate goon.lock.",
}
_CONFIG_FIELDS = frozenset(
    {
        "version",
        "source",
        "kind",
        "name",
        "path",
        "target",
        "repository_id",
        "inputs",
        "agent",
        "implementation",
        "profile",
        "model_profile",
        "acceptance",
        "repository_root",
        "skill_root",
        "repair",
        "review_passes",
        "write_roots",
        "timeout_seconds",
        "step_timeout_seconds",
    }
)
_NEXT_ACTIONS = {
    "EXTERNAL_INPUT_NOT_OBSERVED": "Inspection does not read environment-backed inputs; explicit lock and run use the original environment authority.",
    "UNKNOWN_INPUT": "Inspect the accepted input names; remove unrecognized caller inputs.",
    "MISSING_REQUIRED_INPUT": "Supply required caller values, then retry validation.",
    "INVALID_INPUT_VALUE": "Inspect the owning Goon's type and constraints; correct the caller input value.",
    "INVALID_INPUT_DEFAULT": "Correct the default in the owning Goon input definition.",
    "UNOWNED_PROJECTED_INPUT": "Expose the input through one selected Goon or author an explicit binding.",
    "AMBIGUOUS_PROJECTED_INPUT": "Select recipient imports explicitly with pass: or with: for each shared input.",
}


class SafePlanValidationDiagnostic(TypedDict):
    schema: str
    status: str
    stage: str
    code: str
    inputs: list[str]
    safe_next_action: str
    execution: str
    fields: NotRequired[list[str]]


def safe_plan_diagnostic(exc: Exception) -> SafePlanValidationDiagnostic:
    """Project a *single* received rejection without a second semantic decision."""
    fields: list[str] = []
    if isinstance(exc, RunConfigurationInvalid) and exc.diagnostic_code in _CONFIG_ACTIONS:
        code = exc.diagnostic_code
        inputs = []
        stage = "run_configuration"
        action = _CONFIG_ACTIONS[code]
        fields = sorted(
            {
                field
                for field in exc.fields
                if field and all(part in _CONFIG_FIELDS for part in field.split("."))
            }
        )
    elif isinstance(exc, FleetPlanInputRejected) and exc.diagnostic_code in _NEXT_ACTIONS:
        code = exc.diagnostic_code
        inputs = sorted({name for name in exc.input_names if _IDENTIFIER.fullmatch(name)})
        stage = "input_contract"
        action = _NEXT_ACTIONS[code]
    else:
        code = "PLAN_RESOLUTION_REJECTED"
        inputs = []
        stage = "plan_resolution"
        action = "Review the Plan/Goon source and caller input contract, then retry validation."
    report: SafePlanValidationDiagnostic = {
        "schema": _SCHEMA,
        "status": "rejected",
        "stage": stage,
        "code": code,
        "inputs": inputs,
        "safe_next_action": action,
        "execution": "not_started_by_diagnostic",
    }
    if isinstance(exc, RunConfigurationInvalid):
        report["fields"] = fields
    return report
