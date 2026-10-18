from __future__ import annotations

from oh_goon.core.capability_model import (
    CapabilityDefinition,
    CapabilityRunnerKind,
    RecipeExecutionPolicy,
)
from oh_goon.core.models.capability import CapabilityTargetKind
from oh_goon.core.models.explanation import ReasonCode
from oh_goon.core.vocabulary import CommandSafety, OperationAction, UvRecipeId, Verdict


def _recipe(
    recipe: UvRecipeId,
    label: str,
    description: str,
    safety: CommandSafety,
    argv: tuple[str, ...],
    *,
    parallel_safe: bool = True,
    structured_json: bool = False,
    temporary_output: bool = False,
    append_task_argv: bool = False,
    audit_findings: bool = False,
    failure_reason: ReasonCode = ReasonCode.OPERATION_FAILED,
    failure_verdict: Verdict = Verdict.FAILED,
    user_configurable: bool = False,
    produces_sbom: bool = False,
    invalidates_all: bool = False,
) -> CapabilityDefinition:
    return CapabilityDefinition(
        capability_id=f"recipe:{recipe.value.lower().replace('_', '-')}",
        action=OperationAction.RECIPE,
        label=label,
        description=description,
        runner_kind=CapabilityRunnerKind.RECIPE,
        target_kind=CapabilityTargetKind.RECIPE,
        parallel_safe=parallel_safe,
        schedule_supported=safety is CommandSafety.READ_ONLY,
        safety=safety,
        recipe=recipe,
        user_configurable=user_configurable,
        produces_security_findings=audit_findings,
        produces_sbom=produces_sbom,
        invalidates_all=invalidates_all,
        recipe_execution=RecipeExecutionPolicy(
            argv=argv,
            structured_json=structured_json,
            temporary_output=temporary_output,
            append_task_argv=append_task_argv,
            audit_findings=audit_findings,
            failure_reason=failure_reason,
            failure_verdict=failure_verdict,
        ),
    )


RECIPE_CAPABILITIES: tuple[CapabilityDefinition, ...] = (
    _recipe(
        UvRecipeId.LOCK_CHECK,
        "Lock check",
        "Verify uv.lock is present and up to date without changing it.",
        CommandSafety.READ_ONLY,
        ("uv", "lock", "--check"),
        failure_reason=ReasonCode.LOCK_DRIFT,
        failure_verdict=Verdict.MANUAL_ACTION_REQUIRED,
    ),
    _recipe(
        UvRecipeId.OUTDATED,
        "Outdated dependencies",
        "Show the latest available version for dependencies in the project tree.",
        CommandSafety.READ_ONLY,
        ("uv", "tree", "--outdated", "--frozen"),
    ),
    _recipe(
        UvRecipeId.FORMAT_CHECK,
        "Format check",
        "Run the UV/Ruff formatter in check-only mode.",
        CommandSafety.READ_ONLY,
        ("uv", "--preview-features", "format-command", "format", "--check"),
        failure_reason=ReasonCode.VERIFICATION_FAILED,
    ),
    _recipe(
        UvRecipeId.TYPE_CHECK,
        "Type check",
        "Run UV project checks (currently ty type checking).",
        CommandSafety.READ_ONLY,
        ("uv", "--preview-features", "check-command", "check", "--isolated", "--frozen"),
        failure_reason=ReasonCode.VERIFICATION_FAILED,
    ),
    _recipe(
        UvRecipeId.AUDIT,
        "Security audit",
        "Audit locked project dependencies for vulnerabilities and adverse package status.",
        CommandSafety.READ_ONLY,
        ("uv", "--preview", "audit", "--frozen", "--output-format", "json"),
        structured_json=True,
        audit_findings=True,
    ),
    _recipe(
        UvRecipeId.SYNC_FROZEN,
        "Frozen sync",
        "Synchronize the local project environment without changing uv.lock.",
        CommandSafety.ENVIRONMENT_MUTATION,
        ("uv", "sync", "--frozen"),
    ),
    _recipe(
        UvRecipeId.BUILD,
        "Build package",
        "Build distributions into an isolated temporary output directory.",
        CommandSafety.READ_ONLY,
        ("uv", "build", "--out-dir", "{tempdir}"),
        temporary_output=True,
        failure_reason=ReasonCode.VERIFICATION_FAILED,
    ),
    _recipe(
        UvRecipeId.SBOM,
        "CycloneDX SBOM",
        "Export the locked dependency graph as CycloneDX 1.5 JSON.",
        CommandSafety.READ_ONLY,
        (
            "uv",
            "--preview-features",
            "sbom-export",
            "export",
            "--format",
            "cyclonedx1.5",
            "--frozen",
        ),
        structured_json=True,
        produces_sbom=True,
    ),
    _recipe(
        UvRecipeId.RUN_TASK,
        "Project task",
        "Run one executable through uv run with argv-safe arguments.",
        CommandSafety.WORKTREE_MUTATION,
        ("uv", "run", "--frozen"),
        append_task_argv=True,
        user_configurable=True,
        invalidates_all=True,
    ),
)
