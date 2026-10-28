"""Transport-neutral and redacted presentation of typed Plan-owner rejections.

No source access, reclassification or execution is permitted here.
"""

from __future__ import annotations

import re
from typing import NotRequired, TypedDict

from oh_goon.core.errors import (
    ExecutionLockRejected,
    FleetPlanInputRejected,
    FleetPlanSourceAcquisitionRequired,
    RunConfigurationInvalid,
)

_SCHEMA = "oh-goon.plan-validation-diagnostic/v1"
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_-]{0,63}\Z")
_CONFIG_ACTIONS = {
    "CONFIG_SCHEMA_INVALID": "Correct the named run.yml fields using the version 1 selection contract.",
    "CONFIG_PATH_INVALID": "Select an existing regular source contained in the workflow root; remove traversal and symlink aliases.",
    "CONFIG_TARGET_UNAVAILABLE": "Register the target directory with 'oh-goon league add-folder PATH' and select it during initialization with '--repository PATH'; confirm the repository remains included.",
    "CONFIG_CHANGED": "The captured configuration changed; inspect the current run.yml and explicitly regenerate goon.lock.",
}
_CONFIG_FIELDS = frozenset(
    {
        "version",
        "source",
        "kind",
        "name",
        "path",
        "git",
        "ref",
        "plan",
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
_LOCK_ACTIONS = {
    "missing": "goon.lock is missing. Generate it with 'oh-goon lock run.yml', then retry the run.",
    "unreadable": "goon.lock could not be captured as a bounded regular file. Restore a readable lock without symlink aliases and retry; preserve existing Git and Skill pins.",
    "malformed": "goon.lock is malformed or exceeds its size bound. Restore the generated lock from version control before relocking; do not hand edit its pins or digests.",
    "unsupported_version": "goon.lock uses an unsupported format. Use the matching Oh-Goon version or explicitly regenerate the lock after reviewing the selected sources and pins.",
    "invalid_digest": "goon.lock failed its integrity check. Restore the generated lock from version control before relocking; its recorded pins cannot be trusted.",
    "plan_changed": "The Plan's execution policy, root Steps, requirements, Goon order or native Agent selection changed. Review the intended change, then run 'oh-goon lock run.yml'.",
    "goons_changed": "A Goon's execution, requirements or bound input values changed. Inspect the selected contract, review the intended change, then run 'oh-goon lock run.yml'.",
    "configuration_changed": "run.yml changed since locking. Review its source, target, inputs and Agent/repair selections, then run 'oh-goon lock run.yml' before execution or source acquisition.",
    "external_changed": "A file recorded in goon.lock changed. Review the selected configuration and external files, then run 'oh-goon lock run.yml'.",
    "skills_changed": "The selected Skill source pins changed. Review the intended selection, then run 'oh-goon lock run.yml'; use --update-skills only to explicitly refresh those pins.",
    "sources_changed": "The selected Git Plan, Goon or application pins changed. Review the selection, then run 'oh-goon lock run.yml'; use --update-goons or --update-applications only for an intentional refresh.",
    "execution_changed": "The resolved execution differs from goon.lock. Review the intended change, then run 'oh-goon lock run.yml'.",
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
    inputs: list[str]
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
    elif isinstance(exc, ExecutionLockRejected) and exc.reason in _LOCK_ACTIONS:
        # Keep the existing transport schema; only the rejecting lock owner
        # supplies the failure reason. Never echo lock paths, names or values.
        code = "PLAN_RESOLUTION_REJECTED"
        inputs = []
        stage = "plan_resolution"
        action = _LOCK_ACTIONS[exc.reason]
        if exc.reason == "goons_changed":
            positions = sorted(
                {
                    position
                    for position in exc.goon_positions
                    if type(position) is int and position > 0
                }
            )
            if positions:
                shown = ", ".join(str(position) for position in positions[:16])
                remainder = f" (+{len(positions) - 16} more)" if len(positions) > 16 else ""
                action += f" Changed top-level Goon occurrences in Plan order: {shown}{remainder}."
    elif isinstance(exc, FleetPlanSourceAcquisitionRequired):
        code = "SOURCE_ACQUISITION_REQUIRED"
        inputs = []
        stage = "plan_resolution"
        action = (
            "Inspect the selected Git contract with 'oh-goon config inspect run.yml --acquire', "
            "or freeze its source and values with 'oh-goon lock run.yml'."
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
