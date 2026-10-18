"""Stdlib-only primitives shared by pre-environment dependency tooling.

Native uv owns dependency graph and lock semantics.  Nothing in this module
normalizes, mirrors, or compares uv.lock package rows.
"""
from __future__ import annotations

import re
from typing import Any

_NAME_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)")


class DependencyResolutionError(RuntimeError):
    pass


HOST_CAPABILITY_DISTINCTION = (
    "Authority distinction: dependency intent comes from backend/pyproject.toml; locked selection comes from backend/uv.lock.\n"
    "Repository uv-cache availability and host/internal-runner importability are separate diagnostic facts.\n"
    "A required artifact absent from the selected uv cache does NOT mean the package is unavailable; it may already be importable in the host/internal tool environment.\n"
    "Host/internal availability never satisfies repository offline-replay, native .venv, BP2, or certification authority.\n"
    "For diagnostic-only compatible host reuse: make test-python-core-diagnostic.\n"
    "Native dependency-complete authority still requires the isolated project environment."
)


def _version_tuple(text: str) -> tuple[int, ...]:
    match = re.search(r"(?<!\d)(\d+)(?:\.(\d+))(?:\.(\d+))?\b", text)
    if not match:
        raise DependencyResolutionError(f"Cannot parse native tool version: {text!r}")
    return tuple(int(part or 0) for part in match.groups())


def _minimum_version(spec: str, *, owner: str) -> tuple[int, ...]:
    match = re.fullmatch(r">=\s*(\d+(?:\.\d+){0,3})", spec.strip())
    if match is None:
        raise DependencyResolutionError(
            f"{owner} must declare a simple >= version requirement, got {spec!r}"
        )
    return tuple(int(part) for part in match.group(1).split("."))


def _bounded_version_match(actual: tuple[int, ...], spec: str, *, owner: str) -> bool:
    text = spec.strip()
    minimum_only = re.fullmatch(r">=\s*(\d+(?:\.\d+){0,3})", text)
    if minimum_only is not None:
        minimum = tuple(int(part) for part in minimum_only.group(1).split("."))
        return actual >= minimum
    bounded = re.fullmatch(
        r">=\s*(\d+(?:\.\d+){0,3})\s*,\s*<\s*(\d+(?:\.\d+){0,3})",
        text,
    )
    if bounded is None:
        raise DependencyResolutionError(
            f"{owner} must declare >=MIN or >=MIN,<MAX for native uv, got {spec!r}"
        )
    minimum = tuple(int(part) for part in bounded.group(1).split("."))
    maximum = tuple(int(part) for part in bounded.group(2).split("."))
    if minimum >= maximum:
        raise DependencyResolutionError(f"{owner} has an invalid native uv range: {spec!r}")
    return minimum <= actual < maximum


def _requirement_name(requirement: str) -> str:
    match = _NAME_RE.match(requirement.strip())
    if not match:
        raise DependencyResolutionError(
            f"Cannot normalize declared dependency requirement: {requirement!r}"
        )
    return match.group(1).lower().replace("_", "-")


def _declared_dependency_requirements(metadata: dict[str, Any]) -> tuple[str, ...]:
    project = metadata.get("project", {})
    requirements: list[str] = [str(item) for item in project.get("dependencies", [])]
    for rows in project.get("optional-dependencies", {}).values():
        requirements.extend(str(item) for item in rows)
    for rows in metadata.get("dependency-groups", {}).values():
        requirements.extend(str(item) for item in rows)
    requirements.extend(str(item) for item in metadata.get("build-system", {}).get("requires", []))
    return tuple(requirements)


def _require_minimum_compatible_dependency_intent(metadata: dict[str, Any]) -> None:
    violations = []
    for requirement in _declared_dependency_requirements(metadata):
        requirement_body = requirement.split(";", 1)[0]
        if "==" in requirement_body or ">=" not in requirement_body:
            violations.append(requirement)
    if violations:
        rendered = ", ".join(repr(item) for item in violations)
        raise DependencyResolutionError(
            "backend/pyproject.toml dependency requirements must use minimum-compatible >= ranges; "
            f"exact == pins or requirements without >= are forbidden: {rendered}"
        )
