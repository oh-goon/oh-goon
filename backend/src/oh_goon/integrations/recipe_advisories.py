from __future__ import annotations

from typing import Any

from oh_goon.core.models.recipe import SecurityFinding


def extract_security_findings(payload: dict[str, Any] | list[Any] | None) -> list[SecurityFinding]:
    """Project native uv audit JSON without interpreting vulnerability results."""
    if not isinstance(payload, dict):
        return []
    vulnerabilities = payload.get("vulnerabilities")
    if not isinstance(vulnerabilities, list):
        return []
    findings: list[SecurityFinding] = []
    for vulnerability in vulnerabilities:
        if not isinstance(vulnerability, dict):
            continue
        advisory_id = vulnerability.get("id")
        dependency = vulnerability.get("dependency")
        if not isinstance(advisory_id, str) or not isinstance(dependency, dict):
            continue
        package = dependency.get("name")
        version = dependency.get("version")
        aliases = vulnerability.get("aliases")
        fixed = vulnerability.get("fix_versions")
        summary = vulnerability.get("summary")
        findings.append(
            SecurityFinding(
                advisory_id=advisory_id,
                package=package if isinstance(package, str) else None,
                version=version if isinstance(version, str) else None,
                aliases=tuple(value for value in aliases if isinstance(value, str))
                if isinstance(aliases, list)
                else (),
                fixed_versions=tuple(value for value in fixed if isinstance(value, str))
                if isinstance(fixed, list)
                else (),
                summary=summary if isinstance(summary, str) else None,
            )
        )
    return sorted(findings, key=lambda item: (item.advisory_id, item.package or ""))
