from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from oh_goon.integrations.canonical_goon_tool_authority import (
    load_canonical_goon_tool_authority,
)
from oh_goon.integrations.workload_execution_requirements import (
    WorkloadExecutionRequirements,
    load_workload_execution_requirements,
)

SCHEMA = "oh-goon.goon-static-admission/v1"
REQUIREMENTS_SCHEMA = 5
REQUIREMENTS_FILE = "backend/goon-execution-requirements.json"
_POLICY_FIELD = "workload_tool_permissions"


@dataclass(frozen=True, slots=True)
class StaticAdmissionIssue:
    code: str
    message: str
    goon: str | None = None
    step_id: str | None = None
    missing_tools: tuple[str, ...] = ()
    excess_tools: tuple[str, ...] = ()
    unresolved_edges: tuple[str, ...] = ()
    authority_paths: tuple[str, ...] = ()

    def as_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "code": self.code,
            "message": self.message,
        }
        if self.goon is not None:
            payload["goon"] = self.goon
        if self.step_id is not None:
            payload["step_id"] = self.step_id
        if self.missing_tools:
            payload["missing_tools"] = list(self.missing_tools)
        if self.excess_tools:
            payload["excess_tools"] = list(self.excess_tools)
        if self.unresolved_edges:
            payload["unresolved_edges"] = list(self.unresolved_edges)
        if self.authority_paths:
            payload["authority_paths"] = list(self.authority_paths)
        return payload


def _sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def load_goon_execution_requirements(
    root: Path,
) -> tuple[dict[str, Any], str, WorkloadExecutionRequirements]:
    """Load independent execution policy plus derived workload authority.

    The JSON file deliberately does *not* restate canonical Goon Steps. The production
    compiler owns Step identity/kind/import/environment/cache/artifact/output semantics.
    This file owns only independent repository permission ceilings for derived workloads.
    """

    path = root / REQUIREMENTS_FILE
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ValueError(f"missing canonical Goon execution policy: {path}") from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid {REQUIREMENTS_FILE}: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema") != REQUIREMENTS_SCHEMA:
        raise ValueError(f"{REQUIREMENTS_FILE} must use schema {REQUIREMENTS_SCHEMA}")
    permissions = payload.get(_POLICY_FIELD)
    if not isinstance(permissions, dict):
        raise ValueError(f"{REQUIREMENTS_FILE} {_POLICY_FIELD} must be an object")

    workloads = load_workload_execution_requirements(root)
    identity_payload = json.dumps(
        {
            "policy": _sha256_bytes(raw),
            "workloads": workloads.identity,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return payload, _sha256_bytes(identity_payload), workloads


def workload_tool_permissions(payload: dict[str, Any]) -> dict[str, tuple[str, ...]]:
    raw_permissions = payload.get(_POLICY_FIELD)
    if not isinstance(raw_permissions, dict):
        return {}
    result: dict[str, tuple[str, ...]] = {}
    for owner, raw_tools in raw_permissions.items():
        if not isinstance(owner, str) or not owner:
            continue
        if not isinstance(raw_tools, list):
            continue
        if any(not isinstance(tool, str) or not tool for tool in raw_tools):
            continue
        result[owner] = tuple(raw_tools)
    return result


def canonical_step_required_tools(root: Path, *, goon: str, step_id: str) -> tuple[str, ...]:
    """Return the generated exact tool projection for one canonical Step.

    The projection is derived from compiled canonical Step semantics plus workload
    discovery. It is transport evidence, not an independent semantic owner.
    """

    authority = load_canonical_goon_tool_authority(
        root.resolve() / "backend/src/oh_goon/canonical_goon_tool_authority.json"
    )
    try:
        return authority[goon][step_id]
    except KeyError as exc:
        raise ValueError(
            f"canonical Step has no generated tool authority: {goon}/{step_id}"
        ) from exc


def validate_goon_execution_requirements(
    payload: dict[str, Any], workloads: WorkloadExecutionRequirements
) -> list[StaticAdmissionIssue]:
    """Validate only the independent policy shape, never compiled Goon truth."""

    issues: list[StaticAdmissionIssue] = []
    permissions = payload.get(_POLICY_FIELD)
    if not isinstance(permissions, dict):
        return [
            StaticAdmissionIssue(
                code="INVALID_TOOL_PERMISSION_POLICY",
                message=f"{_POLICY_FIELD} must be an object",
            )
        ]
    for owner, raw_tools in sorted(permissions.items(), key=lambda item: str(item[0])):
        if not isinstance(owner, str) or not owner.startswith("make:"):
            issues.append(
                StaticAdmissionIssue(
                    code="INVALID_TOOL_PERMISSION_OWNER",
                    message=(
                        f"workload permission owner must be a make:<target> identity: {owner!r}"
                    ),
                )
            )
            continue
        if not isinstance(raw_tools, list) or any(
            not isinstance(tool, str) or not tool for tool in raw_tools
        ):
            issues.append(
                StaticAdmissionIssue(
                    code="INVALID_TOOL_PERMISSION",
                    message=f"{owner} permission must be a list of non-empty tool names",
                )
            )
            continue
        if raw_tools != sorted(set(raw_tools)):
            issues.append(
                StaticAdmissionIssue(
                    code="NON_CANONICAL_TOOL_PERMISSION",
                    message=f"{owner} permission must be unique and sorted",
                )
            )
        if owner not in workloads.tools_by_owner:
            issues.append(
                StaticAdmissionIssue(
                    code="TOOL_PERMISSION_OWNER_MISSING",
                    message=f"workload permission owner does not exist: {owner}",
                )
            )
    return issues
