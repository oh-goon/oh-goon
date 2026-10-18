from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path
from typing import Any


_SOURCE_VERSION = re.compile(r"(?m)^source_version:[ \t]*([^\s#]+)[ \t]*$")


def _json_value(path: Path, *keys: str) -> str | None:
    try:
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    for key in keys:
        if not isinstance(payload, dict):
            return None
        payload = payload.get(key)
    return payload if isinstance(payload, str) else None


def _backend_version(path: Path) -> str | None:
    try:
        payload = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return None
    project = payload.get("project")
    value = project.get("version") if isinstance(project, dict) else None
    return value if isinstance(value, str) else None


def observed_source_versions(root: Path) -> dict[str, str | None]:
    owners: dict[str, str | None] = {}
    backend = root / "backend/pyproject.toml"
    package = root / "frontend/package.json"
    package_lock = root / "frontend/package-lock.json"
    openapi = root / "generated/openapi/openapi.json"
    if backend.exists():
        owners["backend/pyproject.toml"] = _backend_version(backend)
    if package.exists():
        owners["frontend/package.json"] = _json_value(package, "version")
    if package_lock.exists():
        owners["frontend/package-lock.json"] = _json_value(package_lock, "version")
        owners["frontend/package-lock.json packages['']"] = _json_value(
            package_lock, "packages", "", "version"
        )
    if openapi.exists():
        owners["generated/openapi/openapi.json"] = _json_value(openapi, "info", "version")

    certification_root = root / "docs/certification"
    if certification_root.is_dir():
        for path in sorted(certification_root.rglob("*.yml")):
            text = path.read_text(encoding="utf-8")
            match = _SOURCE_VERSION.search(text)
            if match is not None:
                owners[path.relative_to(root).as_posix()] = match.group(1)
    return owners


def source_version_issues(root: Path, expected: str) -> tuple[str, ...]:
    issues: list[str] = []
    for owner, observed in observed_source_versions(root).items():
        if observed is None:
            issues.append(f"{owner} does not expose a readable release version")
        elif observed != expected:
            issues.append(f"{owner} version {observed!r} does not match VERSION {expected!r}")
    return tuple(issues)

def require_source_version_coherence(root: Path, expected: str) -> None:
    issues = source_version_issues(root, expected)
    if issues:
        raise ValueError(
            "slim source archive refuses release identity drift: " + "; ".join(issues)
        )

