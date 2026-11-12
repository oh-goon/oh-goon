from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from oh_goon.integrations.hashmarks import HashmarksSourceEvidence
from oh_goon.repository_tooling.architecture._launch_contract_control import (
    check_launch_activity_control,
    check_launch_birth_identity,
    check_launch_current_selection,
)
from oh_goon.repository_tooling.architecture._launch_contract_foundation import (
    check_launch_foundation_model,
    check_launch_projection_boundary,
)
from oh_goon.repository_tooling.architecture._launch_contract_navigation import (
    check_launch_navigation,
)
from oh_goon.repository_tooling.architecture._launch_contract_status import (
    check_launch_status_schemas,
)
from oh_goon.repository_tooling.architecture.common import (
    Violation,
    python_source_observation,
    python_source_snapshot,
    source_import_spelling,
)

_LIFECYCLE_APIS = frozenset(
    {"subprocess.Popen", "os.killpg", "uvicorn.Server", "uvicorn.server.Server"}
)


@dataclass(frozen=True, slots=True)
class _LifecycleSourceUses:
    evidence: HashmarksSourceEvidence
    qualified: tuple[dict[str, Any], ...]
    conditional: tuple[dict[str, Any], ...]


def _is_lifecycle_source_use(fact: dict[str, Any]) -> bool:
    if fact.get("kind") not in {"call-site", "conditional-call-binding"}:
        return False
    if fact.get("qualification") not in {"qualified", "conditional"}:
        return False
    target = fact.get("target")
    return (
        isinstance(target, dict)
        and target.get("kind") == "import"
        and source_import_spelling(target) in _LIFECYCLE_APIS
    )


def _runtime_authority_source_uses(root: Path) -> dict[str, _LifecycleSourceUses]:
    """Inventory admitted source uses, preserving conditional delegate evidence.

    Native Source owns aliases, defaults and helper-use relationships. A
    conditional row may require a declared source owner without establishing
    any invocation's runtime target. Unknown calls are not evidence of absence.
    Bounded subprocess.run calls remain outside this lifecycle-source policy.
    """
    observed: dict[str, _LifecycleSourceUses] = {}
    with python_source_snapshot(root):
        for path in (root / "backend/src/oh_goon").rglob("*.py"):
            evidence, packet = python_source_observation(path)
            admitted = tuple(row for row in packet["facts"] if _is_lifecycle_source_use(row))
            if admitted:
                observed[path.relative_to(root).as_posix()] = _LifecycleSourceUses(
                    evidence,
                    tuple(row for row in admitted if row["qualification"] == "qualified"),
                    tuple(row for row in admitted if row["qualification"] == "conditional"),
                )
    return observed


def _check_runtime_authority_registry(root: Path) -> list[Violation]:
    """Require supported native lifecycle source uses to retain declared owners."""
    import json

    registry_path = root / "docs/development/RUNTIME_AUTHORITY_REGISTRY.json"
    if not registry_path.is_file():
        return [Violation(registry_path, "runtime authority registry is missing")]
    try:
        payload = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [Violation(registry_path, f"runtime authority registry is invalid: {exc}")]
    if not isinstance(payload, dict):
        return [
            Violation(registry_path, "runtime authority registry is invalid: expected an object")
        ]
    owners = payload.get("owners")
    if not isinstance(owners, dict) or not owners:
        return [
            Violation(
                registry_path, "runtime authority registry must classify physical lifecycle owners"
            )
        ]
    if any(not isinstance(domain, str) or not domain.strip() for domain in owners.values()):
        return [
            Violation(
                registry_path,
                "runtime authority registry owners require a nonempty semantic domain",
            )
        ]
    for relative in owners:
        owner_path = Path(relative)
        if (
            owner_path.is_absolute()
            or ".." in owner_path.parts
            or owner_path.as_posix() != relative
            or owner_path.suffix != ".py"
            or owner_path.parts[:3] != ("backend", "src", "oh_goon")
        ):
            return [
                Violation(
                    registry_path,
                    "runtime authority registry owner must be a canonical backend Python path",
                )
            ]

    observed = _runtime_authority_source_uses(root)
    classified = set(owners)
    violations: list[Violation] = []
    for path in sorted(observed.keys() - classified):
        message = (
            "physical process lifecycle authority is unclassified"
            if observed[path].qualified
            else "conditional lifecycle source use is unclassified; runtime target remains unknown"
        )
        violations.append(Violation(root / path, message))
    for path in sorted(classified - observed.keys()):
        violations.append(
            Violation(
                root / path,
                "runtime authority registry entry has no admitted lifecycle source-use evidence; "
                "inspection remains unqualified",
            )
        )
    return violations


def _check_launch_manifest_foundation(root: Path) -> list[Violation]:
    """Keep Launch Manifest policy descriptive/read-only and owner-bound."""
    checks = (
        check_launch_foundation_model,
        check_launch_projection_boundary,
        check_launch_status_schemas,
        check_launch_navigation,
        check_launch_activity_control,
        check_launch_birth_identity,
        check_launch_current_selection,
    )
    violations: list[Violation] = []
    with python_source_snapshot(root):
        for check in checks:
            violations.extend(check(root))
    return violations
