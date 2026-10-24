"""Observe application runtime capabilities inside the uv-materialized project environment.

Native uv owns Python/package compatibility from ``pyproject.toml`` + ``uv.lock`` and
materializes the environment with locked semantics. This diagnostic must not parse
version constraints or independently decide dependency compatibility. It only binds
runtime observations that uv cannot prove by resolution alone: installed distribution
identity, importability, and package-specific runtime probes.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import sys
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import NotRequired, TypedDict

from .hosted_dependency_capability import (
    PYPROJECT,
    HostedCapabilityError,
    _canonical_identity,
    _default_import_probe,
    _import_name,
    _requirement_name,
    _sha256_file,
)

ROOT = Path(__file__).resolve().parents[4]
LOCK = ROOT / "backend/uv.lock"
SCHEMA = "oh-goon.project-runtime-capability/v2"


class ProjectCapabilityReport(TypedDict):
    schema: str
    authority: str
    native_environment_authority: str
    resolution_authority: str
    intent_authority: str
    compatibility_decision: str
    registry_evaluated: bool
    uv_artifact_cache_evaluated: bool
    interpreter: str
    python_version: str
    declared_requires_python: str
    dependency_intent_sha256: str
    dependency_resolution_sha256: str
    declared_runtime_dependencies: list[str]
    packages: list[dict[str, object]]
    blockers: list[str]
    result: str
    identity: NotRequired[str]


def _runtime_dependencies(pyproject: Path) -> tuple[dict[str, str], str]:
    payload = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    project = payload.get("project")
    if not isinstance(project, dict):
        raise HostedCapabilityError("backend/pyproject.toml is missing [project]")
    requires_python = project.get("requires-python")
    if not isinstance(requires_python, str):
        raise HostedCapabilityError("backend/pyproject.toml is missing project.requires-python")
    values = project.get("dependencies", [])
    if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
        raise HostedCapabilityError("project.dependencies must be a string list")
    dependencies: dict[str, str] = {}
    for requirement in values:
        name = _requirement_name(requirement)
        if name in dependencies:
            raise HostedCapabilityError(f"duplicate runtime dependency requirement for {name}")
        dependencies[name] = requirement
    return dependencies, requires_python


def build_project_capability(
    *, pyproject: Path = PYPROJECT, lock: Path = LOCK
) -> ProjectCapabilityReport:
    if not lock.is_file():
        raise HostedCapabilityError(f"committed dependency resolution is missing: {lock}")
    dependencies, requires_python = _runtime_dependencies(pyproject)
    packages: list[dict[str, object]] = []
    blockers: list[str] = []

    for distribution, requirement in dependencies.items():
        import_name = _import_name(distribution)
        row: dict[str, object] = {
            "distribution": distribution,
            "import": import_name,
            "declared_requirement": requirement,
        }
        try:
            row["observed_version"] = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            row.update(status="missing", observed_version=None)
            blockers.append(f"{distribution}: missing from materialized project environment")
            packages.append(row)
            continue
        try:
            _default_import_probe(distribution, import_name)
        except Exception as exc:  # diagnostic boundary classifies runtime/API failures
            detail = f"{type(exc).__name__}: {exc}"
            row.update(status="runtime-broken", detail=detail)
            blockers.append(f"{distribution}: runtime probe failed: {detail}")
        else:
            row["status"] = "observed"
        packages.append(row)

    base: ProjectCapabilityReport = {
        "schema": SCHEMA,
        "authority": "materialized-project-runtime-observation",
        "native_environment_authority": "uv sync --locked",
        "resolution_authority": "backend/uv.lock",
        "intent_authority": "backend/pyproject.toml",
        "compatibility_decision": "native-uv-only",
        "registry_evaluated": False,
        "uv_artifact_cache_evaluated": False,
        "interpreter": str(Path(sys.executable).resolve()),
        "python_version": ".".join(str(part) for part in sys.version_info[:3]),
        "declared_requires_python": requires_python,
        "dependency_intent_sha256": "sha256:" + _sha256_file(pyproject),
        "dependency_resolution_sha256": "sha256:" + _sha256_file(lock),
        "declared_runtime_dependencies": list(dependencies),
        "packages": packages,
        "blockers": blockers,
        "result": (
            "PROJECT-RUNTIME-CAPABILITIES-OBSERVED"
            if not blockers
            else "PROJECT-RUNTIME-CAPABILITIES-BLOCKED"
        ),
    }
    base["identity"] = _canonical_identity(base)
    return base


def _write_atomic(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        payload = build_project_capability()
    except (OSError, HostedCapabilityError) as exc:
        print(f"PROJECT RUNTIME CAPABILITY INVALID: {exc}", file=sys.stderr)
        return 2
    if args.output is not None:
        _write_atomic(args.output, payload)
    for row in payload["packages"]:
        assert isinstance(row, dict)
        print(
            f"{row['distribution']}: {row.get('observed_version') or 'MISSING'} -> {row['status']}"
        )
    print(f"PROJECT_RUNTIME_CAPABILITY_IDENTITY={payload['identity']}")
    print("Compatibility: NATIVE UV AUTHORITY; registry/cache: NOT EVALUATED")
    if payload["result"] != "PROJECT-RUNTIME-CAPABILITIES-OBSERVED":
        print("PROJECT RUNTIME CAPABILITIES BLOCKED", file=sys.stderr)
        for blocker in payload["blockers"]:
            print(f"- {blocker}", file=sys.stderr)
        return 3
    print("PROJECT RUNTIME CAPABILITIES OBSERVED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
