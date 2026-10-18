from __future__ import annotations

import hashlib
import re
import tomllib
from pathlib import Path
from typing import Any

from oh_goon.core.dependency_identity import normalize_package_name
from oh_goon.core.models.fleet_observation import (
    DependencyObservation,
    DependencyObservationSnapshot,
    ObservationSourceEvidence,
)

_REQUIREMENT_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)(.*)$")


def observe_python_dependencies(repository: Path) -> DependencyObservationSnapshot:
    """Return one complete typed PyPI snapshot without executing repository code."""
    root_path = repository / "pyproject.toml"
    lock_path = repository / "uv.lock"
    if not root_path.is_file():
        raise ValueError("Dependency observation requires pyproject.toml")
    if not lock_path.is_file():
        raise ValueError("Dependency observation requires uv.lock")

    root_payload = root_path.read_bytes()
    root_sha256 = hashlib.sha256(root_payload).hexdigest()
    root = _load_toml_bytes(root_payload, "pyproject.toml")
    del root_payload

    lock_payload = lock_path.read_bytes()
    lock_sha256 = hashlib.sha256(lock_payload).hexdigest()
    lock = _load_toml_bytes(lock_payload, "uv.lock")
    del lock_payload

    sources = {"pyproject.toml": root_sha256, "uv.lock": lock_sha256}
    declarations: dict[str, tuple[str | None, str]] = {}
    _collect_declarations(root, "pyproject.toml", declarations)
    _collect_workspace_declarations(repository, root, declarations, sources)
    resolved = _collect_resolved(lock)
    return DependencyObservationSnapshot(
        sources=_source_evidence(sources),
        dependencies=_merge_dependency_evidence(declarations, resolved),
    )


def _collect_workspace_declarations(
    repository: Path,
    root: dict[str, Any],
    declarations: dict[str, tuple[str | None, str]],
    sources: dict[str, str],
) -> None:
    workspace = ((root.get("tool") or {}).get("uv") or {}).get("workspace") or {}
    members = workspace.get("members") or [] if isinstance(workspace, dict) else []
    excluded = {
        str(value)
        for value in (workspace.get("exclude") or [] if isinstance(workspace, dict) else [])
        if isinstance(value, str)
    }
    for member in members:
        if not isinstance(member, str) or member in excluded:
            continue
        for path in sorted(repository.glob(f"{member}/pyproject.toml")):
            relative = path.relative_to(repository).as_posix()
            payload = path.read_bytes()
            sources[relative] = hashlib.sha256(payload).hexdigest()
            parsed = _load_toml_bytes(payload, relative)
            del payload
            _collect_declarations(parsed, relative, declarations)


def _collect_resolved(lock: dict[str, Any]) -> dict[str, set[str]]:
    packages = lock.get("package") or []
    if not isinstance(packages, list):
        raise ValueError("uv.lock package table must be an array")
    resolved: dict[str, set[str]] = {}
    for item in packages:
        if not isinstance(item, dict):
            continue
        name, version = item.get("name"), item.get("version")
        if isinstance(name, str) and isinstance(version, str) and version:
            resolved.setdefault(normalize_package_name(name), set()).add(version)
    return resolved


def _merge_dependency_evidence(
    declarations: dict[str, tuple[str | None, str]], resolved: dict[str, set[str]]
) -> tuple[DependencyObservation, ...]:
    result: list[DependencyObservation] = []
    for name in sorted(set(declarations) | set(resolved)):
        declared = declarations.get(name)
        versions = sorted(resolved.get(name, ()))
        if not versions and declared is not None:
            result.append(
                DependencyObservation(
                    package_name=name,
                    relationship="direct",
                    declared_range=declared[0],
                    declared_source=declared[1],
                )
            )
        for version in versions:
            result.append(
                DependencyObservation(
                    package_name=name,
                    relationship="direct" if declared is not None else "transitive",
                    declared_range=declared[0] if declared is not None else None,
                    declared_source=declared[1] if declared is not None else None,
                    resolved_version=version,
                    resolved_source="uv.lock",
                )
            )
    return tuple(result)


def _source_evidence(sources: dict[str, str]) -> tuple[ObservationSourceEvidence, ...]:
    return tuple(
        ObservationSourceEvidence(path=path, sha256=sha256)
        for path, sha256 in sorted(sources.items())
    )


def _load_toml_bytes(payload: bytes, label: str) -> dict[str, Any]:
    try:
        return tomllib.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"Cannot parse dependency observation source {label}: {exc}") from exc


def _collect_declarations(
    data: dict[str, Any], source: str, result: dict[str, tuple[str | None, str]]
) -> None:
    project = data.get("project") or {}
    groups: list[object] = []
    if isinstance(project, dict):
        groups.append(project.get("dependencies") or [])
        optional = project.get("optional-dependencies") or {}
        if isinstance(optional, dict):
            groups.extend(optional.values())
    dependency_groups = data.get("dependency-groups") or {}
    if isinstance(dependency_groups, dict):
        groups.extend(dependency_groups.values())
    for values in groups:
        if not isinstance(values, list):
            continue
        for value in values:
            if isinstance(value, str) and (match := _REQUIREMENT_NAME.match(value)) is not None:
                name = normalize_package_name(match.group(1))
                result.setdefault(name, (match.group(2).strip() or "*", source))
