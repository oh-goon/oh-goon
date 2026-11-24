"""Transport-neutral and redacted presentation of typed Plan-owner rejections.

No source access, reclassification or execution is permitted here.
"""

from __future__ import annotations

import re
from typing import NotRequired, TypedDict

from oh_goon.core.errors import (
    DurableStateBusy,
    ExecutionBusy,
    ExecutionLockRejected,
    FleetDomainError,
    FleetPlanInputRejected,
    FleetPlanSourceAcquisitionRequired,
    PiSessionRefusalCode,
    ReviewedRepositorySourceStale,
    RunConfigurationInvalid,
    RunControlOwnerUnavailable,
    RunResourceScopeUnavailable,
    RuntimeSafetyBlocked,
)
from oh_goon.core.models.agent_providers import PiSessionHostPreflightUnavailable

_SCHEMA = "oh-goon.plan-validation-diagnostic/v1"
_PI_HANDOFF = "Inspect the original Run with 'oh-goon runs handoff RUN_ID --json'."
_PI_SESSION_ACTIONS: dict[str, str] = {
    PiSessionRefusalCode.NO_PERSISTENT_SESSION: "Select a Run with a retained persistent Pi session.",
    PiSessionRefusalCode.UNAVAILABLE: (
        "Check the matching runtime and dedicated managed Pi RPC profile. Select the latest "
        "qualified writer or explicitly start a fresh session."
    ),
    PiSessionRefusalCode.BUSY: (
        "Wait for the original writer to settle before selecting the latest qualified Run."
    ),
    PiSessionRefusalCode.IDENTITY_MISMATCH: (
        "Use the original registered repository and matching runtime. Preserve the retained history."
    ),
    PiSessionRefusalCode.CLEANUP_UNVERIFIED: (
        "Use the original runtime's recovery evidence to establish process cleanup before continuing."
    ),
    PiSessionRefusalCode.NATIVE_CONFIGURATION_MISMATCH: (
        "Use the matching installed Pi implementation and a qualified managed Pi RPC profile."
    ),
    PiSessionRefusalCode.HISTORY_UNQUALIFIED: (
        "Preserve the retained history. Select a qualified session or explicitly start a fresh one."
    ),
    PiSessionRefusalCode.SETTLEMENT_UNCERTAIN: (
        "Preserve the original evidence and use its matching runtime's recovery. Avoid replaying unresolved effects."
    ),
}
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_-]{0,63}\Z")
_CONFIG_ACTIONS = {
    "CONFIG_SCHEMA_INVALID": "Correct the named version 2 run.yml fields; select literal directory paths with target.repository or target.repositories.",
    "CONFIG_PATH_INVALID": "Select an existing regular source contained in the workflow root; remove traversal and symlink aliases.",
    "CONFIG_TARGET_UNAVAILABLE": "Select exact target roots in target.repository or target.repositories; use 'oh-goon workflow prepare run.yml --register-target' for explicit registration, and confirm every target remains included.",
    "CONFIG_CHANGED": "The captured configuration changed; inspect the current run.yml and explicitly regenerate goon.lock.",
    "CONFIG_REVIEWED_SOURCE_LAYOUT": "Keep run.yml and its goon.lock outside the repository whose source is reviewed, then inspect that exact registered target with --review-source. Do not change source exclusions to hide workflow authoring.",
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
        "repository",
        "repositories",
        "reviewed_source",
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
    "DUPLICATE_INPUT": "Supply each input once using either --input NAME=VALUE or --input-null NAME.",
    "EXTERNAL_INPUT_NOT_OBSERVED": "This observation did not read the external input; explicit prepare and run use its original source owner.",
    "UNKNOWN_INPUT": "Inspect the accepted input names; remove unrecognized caller inputs.",
    "MISSING_REQUIRED_INPUT": "Supply required caller values, then retry validation.",
    "INVALID_INPUT_VALUE": "Inspect the owning Goon's type and constraints; correct the caller input value.",
    "INVALID_INPUT_DEFAULT": "Correct the default in the owning Goon input definition.",
    "UNOWNED_PROJECTED_INPUT": "Expose the input through one selected Goon or author an explicit binding.",
    "AMBIGUOUS_PROJECTED_INPUT": "Select recipient imports explicitly with pass: or with: for each shared input.",
}
_LOCK_ACTIONS = {
    "missing": "goon.lock is missing. Generate it with 'oh-goon workflow prepare run.yml', then retry the run.",
    "unreadable": "goon.lock could not be captured as a bounded regular file. Restore a readable lock without symlink aliases and retry; preserve existing Git and Skill pins.",
    "malformed": "goon.lock is malformed or exceeds its size bound. Restore the generated lock from version control before relocking; do not hand edit its pins or digests.",
    "unsupported_version": "goon.lock uses an unsupported format. Use the matching Oh-Goon version or explicitly regenerate the lock after reviewing the selected sources and pins.",
    "invalid_digest": "goon.lock failed its integrity check. Restore the generated lock from version control before relocking; its recorded pins cannot be trusted.",
    "plan_changed": "The Plan's execution policy, root Steps, requirements, Goon order or native Agent selection changed. Review the intended change, then run 'oh-goon workflow prepare run.yml'.",
    "goons_changed": "A Goon's execution, requirements or bound input values changed. Inspect the selected contract, review the intended change, then run 'oh-goon workflow prepare run.yml'.",
    "configuration_changed": "run.yml changed since locking. Review its source, target, inputs and Agent/repair selections, then run 'oh-goon workflow prepare run.yml' before execution or source acquisition.",
    "external_changed": "A file recorded in goon.lock changed. Review the selected configuration and external files, then run 'oh-goon workflow prepare run.yml'.",
    "skills_changed": "The selected Skill source pins changed. Review the intended selection, then run 'oh-goon workflow prepare run.yml'; use --refresh skills only to explicitly refresh those pins.",
    "sources_changed": "The selected Git Plan, Goon or application pins changed. Review the selection, then run 'oh-goon workflow prepare run.yml'; use --refresh goons or --refresh applications only for an intentional refresh.",
    "execution_changed": "The resolved execution differs from goon.lock. Review the intended change, then run 'oh-goon workflow prepare run.yml'.",
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


def pi_session_safe_next_action(refusal: str | FleetDomainError) -> str | None:
    if isinstance(refusal, PiSessionHostPreflightUnavailable):
        return str(refusal)
    action = _PI_SESSION_ACTIONS.get(
        refusal.code if isinstance(refusal, FleetDomainError) else refusal
    )
    if action is None:
        return None
    return f"{_PI_HANDOFF} {action}"


def safe_plan_diagnostic(exc: Exception) -> SafePlanValidationDiagnostic:
    """Project a *single* received rejection without a second semantic decision."""
    fields: list[str] = []
    inputs: list[str]
    pi_action = pi_session_safe_next_action(exc) if isinstance(exc, FleetDomainError) else None
    if pi_action is not None and isinstance(exc, FleetDomainError):
        code = exc.code
        inputs = []
        stage = "pi_session"
        action = pi_action
    elif isinstance(exc, RunConfigurationInvalid) and exc.diagnostic_code in _CONFIG_ACTIONS:
        code = exc.diagnostic_code
        inputs = []
        stage = "run_configuration"
        action = _CONFIG_ACTIONS[code]
        fields = sorted(
            {
                field
                for field in exc.fields
                if field
                and (
                    all(part in _CONFIG_FIELDS for part in field.split("."))
                    or re.fullmatch(r"target\.repositories\.[0-9]{1,5}", field)
                )
            }
        )
    elif isinstance(exc, RunResourceScopeUnavailable):
        code = exc.code
        inputs = []
        stage = "run_resources"
        action = (
            "Inspect the selected Run resource caps and use a supported native user-manager "
            "context that preserves the caller's existing resource constraints before retrying."
        )
    elif isinstance(exc, ReviewedRepositorySourceStale):
        code = exc.code
        inputs = []
        stage = "repository_admission"
        action = (
            "The selected repository changed since source review. Remove the stale "
            "target.reviewed_source to request a fresh inspection with "
            "'oh-goon workflow inspect run.yml --review-source --json', repeat discovery "
            "and metadata review, then update target.reviewed_source and prepare the workflow. "
            "Relocking alone does not approve changed source."
        )
    elif isinstance(exc, RunControlOwnerUnavailable):
        code = exc.code
        inputs = []
        stage = "run_admission"
        action = (
            "Use the original managed backend, or remove inherited OH_GOON_LAUNCH_ID, "
            "OH_GOON_LAUNCH_OWNER_GENERATION and OH_GOON_LAUNCH_BACKEND_GENERATION from "
            "the new CLI environment before requesting a fresh Run. Inspect existing Runs first."
        )
    elif isinstance(exc, (DurableStateBusy, ExecutionBusy)):
        execution_busy = isinstance(exc, ExecutionBusy)
        code = exc.code if execution_busy else "PLAN_RESOLUTION_REJECTED"
        inputs = []
        stage = "execution_admission" if execution_busy else "plan_resolution"
        action = (
            "Wait for active execution to settle, or inspect retained ownership through "
            "the original matching runtime's recovery tools; then retry."
        )
    elif isinstance(exc, RuntimeSafetyBlocked):
        code = "PLAN_RESOLUTION_REJECTED"
        inputs = []
        stage = "plan_resolution"
        action = (
            "Inspect the matching runtime's safety and recovery findings; resolve "
            "the reported authority before retrying."
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
            "Inspect the selected Git contract with 'oh-goon workflow inspect run.yml --acquire', "
            "or freeze its source and values with 'oh-goon workflow prepare run.yml'."
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
