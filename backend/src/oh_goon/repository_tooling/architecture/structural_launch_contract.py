from __future__ import annotations

import ast
from pathlib import Path

from oh_goon.repository_tooling.architecture.common import Violation, python_ast

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

def _observed_runtime_authority_owners(root: Path) -> set[str]:
    observed: set[str] = set()
    for path in (root / "backend/src/oh_goon").rglob("*.py"):
        tree = python_ast(path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            dotted = (
                f"{func.value.id}.{func.attr}"
                if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
                else None
            )
            if dotted in {"subprocess.Popen", "os.killpg", "uvicorn.Server"}:
                observed.add(path.relative_to(root).as_posix())
    return observed


def _check_runtime_authority_registry(root: Path) -> list[Violation]:
    """Require every physical Python process-lifecycle owner to be explicitly classified."""
    import json

    registry_path = root / "docs/development/RUNTIME_AUTHORITY_REGISTRY.json"
    if not registry_path.is_file():
        return [Violation(registry_path, "runtime authority registry is missing")]
    try:
        payload = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [Violation(registry_path, f"runtime authority registry is invalid: {exc}")]
    owners = payload.get("owners")
    if not isinstance(owners, dict) or not owners:
        return [Violation(registry_path, "runtime authority registry must classify physical lifecycle owners")]

    observed = _observed_runtime_authority_owners(root)
    classified = set(owners)
    violations: list[Violation] = []
    for path in sorted(observed - classified):
        violations.append(Violation(root / path, "physical process lifecycle authority is unclassified"))
    for path in sorted(classified - observed):
        violations.append(Violation(root / path, "runtime authority registry entry is stale; no physical lifecycle primitive remains"))
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
    for check in checks:
        violations.extend(check(root))
    return violations
