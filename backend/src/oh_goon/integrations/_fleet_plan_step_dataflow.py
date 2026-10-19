from __future__ import annotations

import hashlib
import os
import re
import stat
from collections.abc import Mapping
from pathlib import Path

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.fleet_plan import (
    PathExistsCondition,
    PathMissingCondition,
    PlanStreamOutput,
    ResolvedExtensionStep,
    ResolvedKubernetesRolloutVerifyStep,
    ResolvedKubernetesSetImageStep,
    ResolvedLocalSearchAgentStep,
    ResolvedOciTagAssertStep,
    ResolvedPlanStep,
    StepStatusCondition,
)
from oh_goon.core.models.fleet_plan_evidence import (
    PlanStepExecution,
    PublishedPlanOutputEvidence,
)
from oh_goon.core.path_authority import RepositoryPathSpace, repository_relative_parts

_OUTPUT_REFERENCE = re.compile(
    r"\$\{\{\s*steps\.([A-Za-z0-9_.\-\[\]=]+)\.outputs\."
    r"([A-Za-z_][A-Za-z0-9_-]{0,63})\s*\}\}"
)


def condition_skip_reason(
    repository: Path,
    step: ResolvedPlanStep,
    prior: tuple[PlanStepExecution, ...],
) -> str | None:
    condition = getattr(step, "when", None)
    if condition is None:
        return None
    if isinstance(condition, PathExistsCondition):
        path = repository_relative_path(
            repository, condition.path, kind=f"condition for {step.step_id}"
        )
        return None if path.exists() else f"Condition path does not exist: {condition.path}"
    if isinstance(condition, PathMissingCondition):
        path = repository_relative_path(
            repository, condition.path, kind=f"condition for {step.step_id}"
        )
        return None if not path.exists() else f"Condition path exists: {condition.path}"
    if isinstance(condition, StepStatusCondition):
        match = next(item for item in prior if item.step_id == condition.step_id)
        return (
            None
            if match.status in condition.statuses
            else (
                f"Condition step {condition.step_id} status {match.status} not in "
                f"{', '.join(condition.statuses)}"
            )
        )
    raise FleetPlanInvalid(f"Unsupported Fleet plan condition: {type(condition).__name__}")


def validate_condition_path(repository: Path, condition) -> None:
    if isinstance(condition, (PathExistsCondition, PathMissingCondition)):
        repository_relative_path(repository, condition.path, kind="condition")


def validate_step_declared_paths(repository: Path, step: ResolvedPlanStep) -> None:
    """Validate repository-contained step paths shared by preview and preflight."""

    validate_condition_path(repository, getattr(step, "when", None))
    for artifact in getattr(step, "artifacts", ()):
        repository_relative_path(repository, artifact.path, kind=f"artifact for {step.step_id}")
    for output in getattr(step, "outputs", ()):
        if isinstance(output, PlanStreamOutput):
            continue
        repository_relative_path(
            repository, output.path, kind=f"published output for {step.step_id}"
        )


def repository_relative_path(repository: Path, value: str, *, kind: str) -> Path:
    return RepositoryPathSpace.from_root(repository).resolve(value, label=f"Fleet plan {kind} path")


def collect_published_outputs(
    repository: Path,
    step: ResolvedPlanStep,
    *,
    producer_execution_identity: str | None = None,
    streams: Mapping[str, str] | None = None,
) -> tuple[dict[str, str], tuple[PublishedPlanOutputEvidence, ...]]:
    values: dict[str, str] = {}
    evidence: list[PublishedPlanOutputEvidence] = []
    for output in getattr(step, "outputs", ()):
        if isinstance(output, PlanStreamOutput):
            if streams is None or output.stream not in streams:
                raise FleetPlanInvalid("Native attempt output is unavailable for publication")
            payload = streams[output.stream].encode("utf-8")
            if len(payload) > output.max_bytes:
                raise FleetPlanInvalid(
                    f"Fleet plan step {step.step_id} output {output.name} exceeds {output.max_bytes} bytes"
                )
        else:
            payload = _read_published_output(
                repository,
                output.path,
                step_id=step.step_id,
                output_name=output.name,
                max_bytes=output.max_bytes,
            )
        try:
            value = payload.decode("utf-8").strip()
        except UnicodeDecodeError as exc:
            raise FleetPlanInvalid(
                f"Fleet plan step {step.step_id} output {output.name} must be UTF-8 text"
            ) from exc
        if "\x00" in value:
            raise FleetPlanInvalid(
                f"Fleet plan step {step.step_id} output {output.name} cannot contain NUL bytes"
            )
        canonical_payload = value.encode("utf-8")
        values[output.name] = value
        evidence.append(
            PublishedPlanOutputEvidence(
                name=output.name,
                sha256=hashlib.sha256(canonical_payload).hexdigest(),
                byte_count=len(canonical_payload),
                producer_execution_identity=producer_execution_identity,
            )
        )
    return values, tuple(evidence)


def _read_published_output(
    repository: Path,
    value: str,
    *,
    step_id: str,
    output_name: str,
    max_bytes: int,
) -> bytes:
    """Read one declared output without following a leaf symlink.

    RepositoryPathSpace remains the containment authority.  The raw authored leaf is
    opened separately with O_NOFOLLOW where supported so a symlink cannot satisfy a
    regular-file output contract merely because its target is repository-contained.
    The bounded descriptor read also prevents an oversized workload output from being
    loaded in full before its declared byte limit is enforced.
    """

    label = f"published output for {step_id}"
    relative = repository_relative_parts(value, label=f"Fleet plan {label} path")
    # Validate canonical containment, including any symlinked parent components.  Do
    # not use the resolved return value for the read because resolving the leaf would
    # erase the distinction between a regular file and a symlink to one.
    repository_relative_path(repository, value, kind=label)
    path = repository.joinpath(*relative.parts)
    if path.is_symlink():
        raise FleetPlanInvalid(
            f"Fleet plan step {step_id} output {output_name} must be a regular "
            f"non-symlink file: {value}"
        )

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError as exc:
        raise FleetPlanInvalid(
            f"Fleet plan step {step_id} did not publish required output {output_name}: {value}"
        ) from exc
    except OSError as exc:
        raise FleetPlanInvalid(
            f"Fleet plan step {step_id} cannot read published output {output_name}: {exc}"
        ) from exc

    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise FleetPlanInvalid(
                f"Fleet plan step {step_id} did not publish required output {output_name}: {value}"
            )
        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining > 0:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
    except OSError as exc:
        raise FleetPlanInvalid(
            f"Fleet plan step {step_id} cannot read published output {output_name}: {exc}"
        ) from exc
    finally:
        os.close(descriptor)

    if len(payload) > max_bytes:
        raise FleetPlanInvalid(
            f"Fleet plan step {step_id} output {output_name} exceeds {max_bytes} bytes"
        )
    return payload


def bind_step_outputs(
    step: ResolvedPlanStep,
    published: Mapping[str, Mapping[str, str]],
) -> ResolvedPlanStep:
    def output_value(source_step: str, output_name: str) -> str:
        try:
            return published[source_step][output_name]
        except KeyError as exc:
            raise FleetPlanInvalid(
                f"Fleet plan step {step.step_id} requires unavailable output "
                f"{source_step}.{output_name}"
            ) from exc

    def bind(value: str) -> str:
        return _OUTPUT_REFERENCE.sub(
            lambda match: output_value(match.group(1), match.group(2)), value
        )

    updates = {}
    if isinstance(step, ResolvedLocalSearchAgentStep) and step.feedback_reference is not None:
        value = bind(step.feedback_reference)
        if not value.strip() or len(value.encode("utf-8")) > 2048:
            raise FleetPlanInvalid(
                "Agent failed-test feedback must be nonempty and at most 2048 UTF-8 bytes"
            )
        updates["feedback_value"] = value
    if hasattr(step, "argv"):
        updates["argv"] = tuple(bind(item) for item in step.argv)
    elif isinstance(step, ResolvedExtensionStep):
        updates["args"] = tuple(bind(item) for item in step.args)
    elif isinstance(step, ResolvedOciTagAssertStep):
        updates["expected_digest"] = bind(step.expected_digest)
    elif isinstance(step, ResolvedKubernetesSetImageStep):
        updates["image"] = bind(step.image)
    elif isinstance(step, ResolvedKubernetesRolloutVerifyStep):
        updates["expected_generation"] = bind(step.expected_generation)
        updates["expected_image"] = bind(step.expected_image)
    return step.model_copy(update=updates) if updates else step
