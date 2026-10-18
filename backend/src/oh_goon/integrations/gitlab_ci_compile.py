from __future__ import annotations

import re
import shlex
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.fleet_plan import (
    DEFAULT_COMMAND_TIMEOUT_SECONDS,
    CommandPlanStep,
    CompiledExecutionTemplate,
    GitNativeAction,
    GitPlanStep,
    MissingConfigPolicy,
    PlanArtifactDeclaration,
    PreCommitPlanStep,
)
from oh_goon.core.models.fleet_plan_execution_policy import CicdExecutionPolicy
from oh_goon.core.models.fleet_plan_migration import (
    GitLabGoonMigration,
    GitLabImportIssue,
)

_SUPPORTED_JOB_FIELDS = {"stage", "needs", "script", "timeout", "artifacts"}
_SHELL_META = re.compile(r"(?:&&|\|\||[;|<>`]|\$\(|\n)")
_GITLAB_VAR = re.compile(r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))")


def compile_native_goon(
    *,
    source: Path,
    payload: dict[str, Any],
    issues: list[GitLabImportIssue],
) -> CompiledExecutionTemplate:
    jobs = payload.get("jobs", {})
    stage_order = _stage_order(payload.get("stages"))
    ordered_jobs = _order_jobs(jobs, stage_order, issues)
    steps = []
    for job_name in ordered_jobs:
        job = jobs[job_name]
        for field in sorted(set(job) - _SUPPORTED_JOB_FIELDS):
            issues.append(
                _unsupported(
                    job_name,
                    field,
                    f"GitLab {field} is outside the faithful Fleet import subset and requires explicit migration",
                )
            )
        scripts = _scripts(job.get("script"))
        if scripts is None:
            issues.append(
                _unsupported(job_name, "script", "script must be a string or list of strings")
            )
            continue
        if len(scripts) != 1:
            issues.append(
                _unsupported(
                    job_name,
                    "script",
                    "GitLab multi-command jobs share one runner shell; Fleet import will not split that hidden shell state across steps",
                )
            )
        artifacts = _artifact_paths(job_name, job.get("artifacts"), issues)
        timeout = _timeout_seconds(job_name, job.get("timeout"), issues)
        for index, script in enumerate(scripts, start=1):
            step_id = _step_id(job_name, index)
            converted = _convert_script(
                job_name,
                step_id,
                script,
                issues=issues,
                timeout_seconds=timeout,
                artifacts=artifacts if index == len(scripts) else (),
            )
            steps.append(converted)
    if not steps:
        raise FleetPlanInvalid("GitLab import produced no executable Fleet steps")
    return CompiledExecutionTemplate(
        name="goon",
        description="",
        execution=CicdExecutionPolicy(),
        requirements=(),
        steps=tuple(steps),
    )


def _order_jobs(
    jobs: dict[str, dict[str, Any]],
    stage_order: dict[str, int],
    issues: list[GitLabImportIssue],
) -> list[str]:
    names = list(jobs)
    dependencies: dict[str, set[str]] = {name: set() for name in names}
    stage_groups: dict[int, list[str]] = defaultdict(list)
    for name, job in jobs.items():
        stage_groups[stage_order.get(str(job.get("stage", "test")), 999)].append(name)
    for name, job in jobs.items():
        needs = job.get("needs")
        if needs is not None:
            parsed = _needs(name, needs, jobs, issues)
            dependencies[name].update(parsed)
        else:
            own_stage = stage_order.get(str(job.get("stage", "test")), 999)
            for stage, stage_jobs in stage_groups.items():
                if stage < own_stage:
                    dependencies[name].update(stage_jobs)
    ready = sorted(name for name in names if not dependencies[name])
    result: list[str] = []
    remaining = {name: set(deps) for name, deps in dependencies.items()}
    concurrency_reported = False
    while ready:
        if len(ready) > 1 and not concurrency_reported:
            issues.append(
                _unsupported(
                    "",
                    "jobs",
                    "GitLab dependency graph permits concurrent jobs "
                    f"({', '.join(ready)}); serial Fleet steps cannot preserve that execution graph",
                )
            )
            concurrency_reported = True
        current = ready.pop(0)
        if current in result:
            continue
        result.append(current)
        for name in sorted(names):
            if current in remaining[name]:
                remaining[name].remove(current)
                if not remaining[name] and name not in result and name not in ready:
                    ready.append(name)
        ready.sort()
    if len(result) != len(names):
        issues.append(
            _unsupported(
                "", "needs", "GitLab job dependency graph contains a cycle or unresolved dependency"
            )
        )
        result.extend(sorted(set(names) - set(result)))
    return result


def _needs(
    job_name: str, value: Any, jobs: dict[str, Any], issues: list[GitLabImportIssue]
) -> set[str]:
    if value == []:
        return set()
    if not isinstance(value, list):
        issues.append(_unsupported(job_name, "needs", "needs must be a list"))
        return set()
    result: set[str] = set()
    for item in value:
        if isinstance(item, str):
            dependency = item
        elif (
            isinstance(item, dict)
            and set(item) <= {"job", "optional", "artifacts"}
            and isinstance(item.get("job"), str)
        ):
            if item.get("optional") or item.get("artifacts") is False:
                issues.append(
                    _unsupported(
                        job_name,
                        "needs",
                        "optional/artifact-modifying needs require manual migration",
                    )
                )
            dependency = item["job"]
        else:
            issues.append(
                _unsupported(
                    job_name,
                    "needs",
                    "cross-project/pipeline/matrix needs require manual migration",
                )
            )
            continue
        if dependency not in jobs:
            issues.append(
                _unsupported(job_name, "needs", f"needs references unknown job {dependency}")
            )
            continue
        result.add(dependency)
    return result


def _convert_script(
    job_name: str,
    step_id: str,
    script: str,
    *,
    issues: list[GitLabImportIssue],
    timeout_seconds: float,
    artifacts: tuple[PlanArtifactDeclaration, ...],
):
    script = script.strip()
    if not script:
        raise FleetPlanInvalid(f"GitLab job produced an empty script for {step_id}")
    referenced = _gitlab_variable_names(script)
    for name in sorted(referenced):
        issues.append(
            _unsupported(
                job_name,
                f"script.${name}",
                f"GitLab runtime variable ${name} has runner/scoped external state; rewrite it as an explicit Fleet input or env source",
            )
        )
    if _SHELL_META.search(script):
        issues.append(
            _unsupported(
                job_name,
                "script.shell",
                "GitLab runner shell semantics are external state; rewrite shell expressions as an explicit Fleet command",
            )
        )
        return _shell_command_step(step_id, script, timeout_seconds, artifacts)
    try:
        argv = tuple(shlex.split(script, posix=True))
    except ValueError:
        issues.append(
            _unsupported(
                job_name,
                "script.shell",
                "GitLab script quoting cannot be represented as direct Fleet argv without selecting runner shell semantics",
            )
        )
        return _shell_command_step(step_id, script, timeout_seconds, artifacts)
    if not argv:
        raise FleetPlanInvalid(f"GitLab job produced an empty argv for {step_id}")
    return _native_step(job_name, step_id, argv, timeout_seconds, artifacts, issues)


def _shell_command_step(
    step_id: str,
    script: str,
    timeout_seconds: float,
    artifacts: tuple[PlanArtifactDeclaration, ...],
) -> CommandPlanStep:
    return CommandPlanStep(
        step_id=step_id,
        argv=("sh", "-lc", script),
        timeout_seconds=timeout_seconds,
        artifacts=artifacts,
    )


def _native_step(
    job_name: str,
    step_id: str,
    argv: tuple[str, ...],
    timeout_seconds: float,
    artifacts: tuple[PlanArtifactDeclaration, ...],
    issues: list[GitLabImportIssue],
):
    if argv[:3] == ("pre-commit", "run", "--all-files") and len(argv) == 3:
        return PreCommitPlanStep(
            step_id=step_id,
            if_missing=MissingConfigPolicy.FAIL,
            timeout_seconds=timeout_seconds,
            artifacts=artifacts,
        )
    if argv[0] == "git" and len(argv) > 1:
        if timeout_seconds > 3600:
            issues.append(
                _unsupported(
                    job_name,
                    "timeout",
                    f"GitLab timeout {timeout_seconds:g}s exceeds Fleet Git step maximum 3600s; rewrite the job explicitly instead of truncating its deadline",
                )
            )
            return CommandPlanStep(
                step_id=step_id,
                argv=argv,
                timeout_seconds=timeout_seconds,
                artifacts=artifacts,
            )
        return GitPlanStep(
            step_id=step_id,
            action=GitNativeAction(args=argv[1:]),
            timeout_seconds=timeout_seconds,
            artifacts=artifacts,
        )
    if timeout_seconds != DEFAULT_COMMAND_TIMEOUT_SECONDS:
        issues.append(
            _unsupported(
                job_name,
                "timeout",
                f"GitLab command timeout {timeout_seconds:g}s cannot be authored on a normal Fleet run: Step; "
                f"remove the source-specific timeout only if the backend default "
                f"{DEFAULT_COMMAND_TIMEOUT_SECONDS:g}s is intentionally equivalent",
            )
        )
    return CommandPlanStep(
        step_id=step_id,
        argv=argv,
        timeout_seconds=DEFAULT_COMMAND_TIMEOUT_SECONDS,
        artifacts=artifacts,
    )


def _gitlab_variable_names(script: str) -> set[str]:
    return {match.group(1) or match.group(2) for match in _GITLAB_VAR.finditer(script)}


def _scripts(value: Any) -> list[str] | None:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return list(value)
    return None


def _artifact_paths(
    job_name: str, raw: Any, issues: list[GitLabImportIssue]
) -> tuple[PlanArtifactDeclaration, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, dict):
        issues.append(_unsupported(job_name, "artifacts", "artifacts must be a mapping"))
        return ()
    unsupported = set(raw) - {"paths"}
    if unsupported:
        issues.append(
            _unsupported(
                job_name,
                "artifacts",
                f"unsupported artifact fields: {', '.join(sorted(unsupported))}",
            )
        )
    paths = raw.get("paths", [])
    if not isinstance(paths, list) or not all(isinstance(item, str) for item in paths):
        issues.append(
            _unsupported(job_name, "artifacts.paths", "artifact paths must be a string list")
        )
        return ()
    return tuple(PlanArtifactDeclaration(path=path) for path in paths)


def _timeout_seconds(job_name: str, raw: Any, issues: list[GitLabImportIssue]) -> float:
    if raw is None:
        issues.append(
            _unsupported(
                job_name,
                "timeout",
                "GitLab project/default timeout is external state; declare a job timeout before import",
            )
        )
        return 3600.0
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        seconds = float(raw)
        if 0 < seconds <= 21600:
            return seconds
        issues.append(
            _unsupported(
                job_name,
                "timeout",
                f"GitLab timeout {raw!r} exceeds Fleet's representable range 0 < timeout <= 21600s; rewrite it explicitly instead of truncating",
            )
        )
        return 3600.0
    if isinstance(raw, str):
        match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(seconds?|minutes?|hours?|s|m|h)\s*", raw, re.I)
        if match:
            amount = float(match.group(1))
            unit = match.group(2).lower()[0]
            factor = {"s": 1, "m": 60, "h": 3600}[unit]
            seconds = amount * factor
            if 0 < seconds <= 21600:
                return seconds
            issues.append(
                _unsupported(
                    job_name,
                    "timeout",
                    f"GitLab timeout {seconds:g}s exceeds Fleet's representable range 0 < timeout <= 21600s; rewrite it explicitly instead of truncating",
                )
            )
            return 3600.0
    issues.append(
        _unsupported(job_name, "timeout", f"GitLab timeout {raw!r} cannot be represented by Fleet")
    )
    return 3600.0


def _stage_order(stages: Any) -> dict[str, int]:
    ordered = [".pre", "build", "test", "deploy", ".post"] if not stages else list(stages)
    return {stage: index for index, stage in enumerate(ordered)}


def _step_id(job: str, index: int) -> str:
    clean = re.sub(r"[^A-Za-z0-9._-]+", "-", job).strip("-") or "job"
    suffix = f"-{index}"
    return f"{clean[: 80 - len(suffix)]}{suffix}"


def native_goon_yaml(migration: GitLabGoonMigration) -> str:
    goon = migration.goon
    if goon.execution is None:
        raise FleetPlanInvalid("GitLab migration requires its resolved execution policy")
    execution = {
        **goon.execution.model_dump(mode="json", exclude_none=True, exclude_defaults=True),
        "purpose": goon.execution.purpose.value,
    }
    payload: dict[str, Any] = {
        "version": goon.version,
        "execution": execution,
    }
    if goon.requirements:
        payload["requirements"] = [
            item.model_dump(mode="json", exclude_none=True) for item in goon.requirements
        ]
    if goon.inputs:
        payload["inputs"] = {
            item.name: {
                "type": item.input_type.value,
                **({"default": item.default} if not item.required else {}),
                **({"description": item.description} if item.description else {}),
                **({"choices": list(item.choices)} if item.choices else {}),
                **({"min": item.minimum} if item.minimum is not None else {}),
                **({"max": item.maximum} if item.maximum is not None else {}),
            }
            for item in goon.inputs
        }
    payload["steps"] = [_step_yaml(step) for step in goon.steps]
    banner = (
        "# Generated by `oh-goon goon import-gitlab`.\n"
        "# This is a native repository Goon. GitLab is not consulted when this Goon runs.\n"
    )
    if migration.components:
        banner += "# Imported components:\n" + "".join(
            f"#   - {item.reference} ({item.source_path}, sha256:{item.content_sha256[:12]})\n"
            for item in migration.components
        )
    return banner + yaml.safe_dump(payload, sort_keys=False)


def _step_yaml(step) -> dict[str, Any]:
    data = step.model_dump(mode="json", exclude_none=True, exclude_defaults=True)
    uses = step.kind
    step_id = step.step_id
    data.pop("kind", None)
    data.pop("step_id", None)
    if uses == "command":
        argv = tuple(str(item) for item in data.pop("argv"))
        data.pop("timeout_seconds", None)
        return {"id": step_id, "run": shlex.join(argv), **data}
    result = {"uses": uses, "id": step_id}
    if uses == "git":
        action = data.pop("action")
        result.update(action)
    result.update(data)
    return result


def _unsupported(job: str, field: str, reason: str) -> GitLabImportIssue:
    return GitLabImportIssue(job=job, field=field, reason=reason)
