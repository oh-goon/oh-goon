from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.fleet_plan_evidence import (
    CicdRepositoryAdmissionEvidence,
    RequirementsRepositoryAdmissionEvidence,
)
from oh_goon.core.models.fleet_plan_requirements import (
    PyprojectValueRequirement,
    RepositoryFileExistsRequirement,
    RepositoryFolderExistsRequirement,
    RepositoryPathExistsRequirement,
    RepositoryRequirement,
    RepositoryValueType,
    UvProjectRequirement,
)
from oh_goon.core.path_authority import RepositoryPathSpace
from oh_goon.core.product_identity import stable_id


@dataclass(frozen=True, slots=True)
class AcceptedCicdRepositoryAdmission:
    evidence: CicdRepositoryAdmissionEvidence


@dataclass(frozen=True, slots=True)
class RejectedCicdRepositoryAdmission:
    issues: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.issues:
            raise ValueError("Rejected CI/CD repository admission requires at least one issue")


CicdRepositoryAdmissionInspection = (
    AcceptedCicdRepositoryAdmission | RejectedCicdRepositoryAdmission
)


def inspect_repository_requirements(
    repository: Path,
    requirements: tuple[RepositoryRequirement, ...] = (),
) -> tuple[str, ...]:
    """Check one Goon-owned requirement set without inventing a merged owner."""
    root = repository.resolve()
    issues: list[str] = []
    needs_pyproject = any(isinstance(item, PyprojectValueRequirement) for item in requirements)
    pyproject = _load_pyproject(root / "pyproject.toml", issues) if needs_pyproject else None
    _validate_repository_requirements(root, pyproject, requirements, issues)
    return tuple(issues)


def inspect_cicd_repository(
    repository: Path,
    requirements: tuple[RepositoryRequirement, ...] = (),
) -> CicdRepositoryAdmissionInspection:
    """Check effective resolved requirements for strict execution admission.

    Git metadata is not source authority here. Strict execution consumes an isolated
    source snapshot, so repository requirements own admissibility and Git remains an
    optional repository-native tool/input when a Plan explicitly asks for it.
    """
    issues = list(inspect_repository_requirements(repository, requirements))

    if issues:
        return RejectedCicdRepositoryAdmission(issues=tuple(issues))
    return AcceptedCicdRepositoryAdmission(
        evidence=_requirements_admission_evidence(requirements),
    )


def validate_cicd_repository(
    repository: Path,
    requirements: tuple[RepositoryRequirement, ...] = (),
) -> CicdRepositoryAdmissionEvidence:
    inspection = inspect_cicd_repository(repository, requirements)
    if isinstance(inspection, AcceptedCicdRepositoryAdmission):
        return inspection.evidence
    rendered = "; ".join(inspection.issues)
    raise FleetPlanInvalid(
        f"Fleet CI/CD repository requirements failed for {repository.name}: {rendered}"
    )


def validate_cicd_snapshot_requirements(
    repository_snapshot: Path,
    requirements: tuple[RepositoryRequirement, ...] = (),
) -> CicdRepositoryAdmissionEvidence:
    """Validate resolved requirements against sanitized execution bytes.

    Git-root topology belongs to managed-source admission before snapshotting. The
    immutable execution snapshot intentionally excludes Git metadata, so replaying
    that topology check here would make a canonical sanitized snapshot invalid by
    construction.
    """
    issues = inspect_repository_requirements(repository_snapshot, requirements)
    if issues:
        rendered = "; ".join(issues)
        raise FleetPlanInvalid(
            f"Fleet CI/CD repository requirements failed for {repository_snapshot.name}: {rendered}"
        )
    return _requirements_admission_evidence(requirements)


def _requirements_admission_evidence(
    requirements: tuple[RepositoryRequirement, ...],
) -> CicdRepositoryAdmissionEvidence:
    requirements_id = stable_id(
        "cicd-repository-requirements",
        [item.model_dump(mode="json") for item in requirements],
    )
    return RequirementsRepositoryAdmissionEvidence(
        requirements_id=requirements_id,
        requirement_count=len(requirements),
    )


def _validate_repository_requirements(
    root: Path,
    pyproject: dict[str, Any] | None,
    requirements: tuple[RepositoryRequirement, ...],
    issues: list[str],
) -> None:
    space = RepositoryPathSpace.from_root(root)
    for requirement in requirements:
        if isinstance(requirement, RepositoryFileExistsRequirement):
            _validate_file_requirement(space, requirement.path, issues)
        elif isinstance(requirement, RepositoryFolderExistsRequirement):
            _validate_folder_requirement(space, requirement.path, issues)
        elif isinstance(requirement, RepositoryPathExistsRequirement):
            _validate_path_requirement(space, requirement, issues)
        elif isinstance(requirement, PyprojectValueRequirement):
            _validate_pyproject_requirement(pyproject, requirement, issues)
        elif isinstance(requirement, UvProjectRequirement):
            _validate_uv_project_requirement(space, requirement, issues)
        else:  # pragma: no cover - closed discriminated union
            raise AssertionError(
                f"Unhandled Fleet repository requirement: {type(requirement).__name__}"
            )


def _resolve_requirement_path(
    space: RepositoryPathSpace, path: str, issues: list[str]
) -> Path | None:
    try:
        return space.resolve(path, label=f"Fleet repository requirement {path}")
    except FleetPlanInvalid as exc:
        issues.append(str(exc))
        return None


def _validate_file_requirement(space: RepositoryPathSpace, path: str, issues: list[str]) -> None:
    candidate = _resolve_requirement_path(space, path, issues)
    if candidate is not None and not candidate.is_file():
        issues.append(f"repository requirement file is missing: {path}")


def _validate_folder_requirement(space: RepositoryPathSpace, path: str, issues: list[str]) -> None:
    candidate = _resolve_requirement_path(space, path, issues)
    if candidate is not None and not candidate.is_dir():
        issues.append(f"repository requirement folder is missing: {path}")


def _validate_path_requirement(
    space: RepositoryPathSpace,
    requirement: RepositoryPathExistsRequirement,
    issues: list[str],
) -> None:
    candidate = _resolve_requirement_path(space, requirement.path, issues)
    if candidate is not None and not candidate.exists():
        issues.append(f"repository requirement path is missing: {requirement.path}")


def _validate_pyproject_requirement(
    pyproject: dict[str, Any] | None,
    requirement: PyprojectValueRequirement,
    issues: list[str],
) -> None:
    if pyproject is None:
        return
    value = _lookup_pyproject_value(pyproject, requirement.field)
    if value is _MISSING:
        issues.append(f"repository requirement pyproject field is missing: {requirement.field}")
        return
    if not _matches_requirement_type(value, requirement.value_type):
        issues.append(
            "repository requirement pyproject field has wrong type: "
            f"{requirement.field} must be {requirement.value_type.value}"
        )
        return
    if requirement.non_empty and isinstance(value, str) and not value.strip():
        issues.append(
            f"repository requirement pyproject field must be non-empty: {requirement.field}"
        )
        return
    if (
        requirement.pattern is not None
        and isinstance(value, str)
        and re.fullmatch(requirement.pattern, value) is None
    ):
        issues.append(
            "repository requirement pyproject field has invalid format: "
            f"{requirement.field} must match {requirement.pattern!r}"
        )


def _validate_uv_project_requirement(
    space: RepositoryPathSpace,
    requirement: UvProjectRequirement,
    issues: list[str],
) -> None:
    project = _resolve_requirement_path(space, requirement.project, issues)
    if project is None:
        return
    if not project.is_dir():
        issues.append(f"selected UV project directory is missing: {requirement.project}")
        return
    if not (project / "pyproject.toml").is_file():
        issues.append(f"selected UV project requires pyproject.toml: {requirement.project}")
    if not (project / "uv.lock").is_file():
        issues.append(f"selected UV project requires uv.lock: {requirement.project}")


_MISSING = object()


def _lookup_pyproject_value(payload: dict[str, Any], field: str) -> object:
    current: object = payload
    for part in field.split("."):
        if not isinstance(current, dict) or part not in current:
            return _MISSING
        current = current[part]
    return current


def _matches_requirement_type(value: object, expected: RepositoryValueType) -> bool:
    if expected is RepositoryValueType.STRING:
        return isinstance(value, str)
    if expected is RepositoryValueType.INTEGER:
        return type(value) is int
    if expected is RepositoryValueType.BOOLEAN:
        return type(value) is bool
    raise AssertionError(f"Unhandled Fleet repository value type: {expected}")


def _load_pyproject(path: Path, issues: list[str]) -> dict[str, Any] | None:
    if not path.is_file():
        issues.append("missing pyproject.toml")
        return None
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        issues.append(f"invalid pyproject.toml: {exc}")
        return None
