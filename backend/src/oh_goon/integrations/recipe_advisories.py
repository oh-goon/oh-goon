from __future__ import annotations

from typing import Any

from oh_goon.core.models.recipe import SecurityFinding


def extract_security_findings(payload: dict[str, Any] | list[Any] | None) -> list[SecurityFinding]:
    """Extract stable advisory essentials from uv audit JSON while retaining the raw payload elsewhere."""
    if payload is None:
        return []
    found: dict[tuple[str, str | None, str | None], SecurityFinding] = {}

    def walk(node: Any, package: str | None = None, version: str | None = None) -> None:
        if isinstance(node, list):
            for item in node:
                walk(item, package, version)
            return
        if not isinstance(node, dict):
            return
        package_value = node.get("package")
        if isinstance(package_value, dict):
            package = str(package_value.get("name") or package or "") or package
            version = str(package_value.get("version") or version or "") or version
        elif isinstance(package_value, str):
            package = package_value
        if isinstance(node.get("name"), str) and any(
            key in node for key in ("version", "vulnerabilities", "advisories")
        ):
            package = str(node["name"])
        if isinstance(node.get("version"), str):
            version = str(node["version"])
        advisory_id = node.get("id") or node.get("advisory_id")
        if isinstance(advisory_id, str) and any(
            key in node for key in ("aliases", "summary", "details", "fixed", "affected")
        ):
            key = (advisory_id, package, version)
            found[key] = SecurityFinding(
                advisory_id=advisory_id,
                package=package,
                version=version,
                aliases=[str(value) for value in node.get("aliases", []) if isinstance(value, str)],
                fixed_versions=_fixed_versions(node),
                summary=str(node.get("summary")) if node.get("summary") else None,
            )
        for key, value in node.items():
            if key not in {"package", "name", "version"}:
                walk(value, package, version)

    walk(payload)
    return sorted(found.values(), key=lambda item: (item.advisory_id, item.package or ""))


def _fixed_versions(node: dict[str, Any]) -> list[str]:
    values: set[str] = set()
    for key in ("fixed_versions", "fix_versions", "fixed"):
        value = node.get(key)
        if isinstance(value, str):
            values.add(value)
        elif isinstance(value, list):
            values.update(str(item) for item in value if isinstance(item, (str, int, float)))
    affected = node.get("affected")
    if isinstance(affected, list):
        for item in affected:
            if not isinstance(item, dict):
                continue
            ranges = item.get("ranges", [])
            for range_item in ranges if isinstance(ranges, list) else []:
                if not isinstance(range_item, dict):
                    continue
                events = range_item.get("events", [])
                for event in events if isinstance(events, list) else []:
                    if isinstance(event, dict) and isinstance(event.get("fixed"), str):
                        values.add(event["fixed"])
    return sorted(values)
