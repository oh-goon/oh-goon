from __future__ import annotations

import re
from pathlib import Path

from oh_goon.repository_tooling.architecture.common import Violation
from oh_goon.repository_tooling.architecture.typescript_semantics import project_typescript


def _typescript_method_parameter_types(text: str, method: str) -> tuple[str, ...] | None:
    match = re.search(rf"\b{re.escape(method)}\s*\(([^)]*)\)", text)
    if match is None:
        return None
    parameters = [item.strip() for item in match.group(1).split(",") if item.strip()]
    types: list[str] = []
    for parameter in parameters:
        if ":" not in parameter:
            return None
        _name, type_name = parameter.split(":", 1)
        types.append(type_name.strip())
    return tuple(types)


def _check_frontend_transport_ownership(root: Path) -> list[Violation]:
    source = root / "frontend" / "src"
    violations: list[Violation] = []
    if not source.exists():
        return violations

    allowed_http_importers = {
        "app/createApiClient.ts",
        "app/api/mutationTransport.ts",
        "app/api/localAccess.ts",
    }
    for path in source.rglob("*"):
        if path.suffix not in {".ts", ".tsx"}:
            continue
        relative = path.relative_to(source).as_posix()
        if path.name.endswith((".test.ts", ".test.tsx")):
            continue
        text = path.read_text(encoding="utf-8")
        facts = project_typescript(text)
        if (
            "@/app/api/httpTransport" in facts.module_imports
            and relative not in allowed_http_importers
        ):
            violations.append(
                Violation(
                    path,
                    "HTTP transport is infrastructure-only; consume ApiClient instead of opening a parallel transport path",
                )
            )
        if (
            "@/app/api/mutationTransport" in facts.module_imports
            and relative != "app/createApiClient.ts"
        ):
            violations.append(
                Violation(
                    path,
                    "writeOperation is owned only by createApiClient; feature/app code must not bypass the mutation boundary",
                )
            )
        if (
            "@/app/createApiClient" in facts.module_imports
            and relative != "app/ApiClientProvider.tsx"
        ):
            violations.append(
                Violation(
                    path,
                    "createApiClient is owned only by ApiClientProvider; consumers must use the provider boundary",
                )
            )

    return violations


def _check_browser_persistence_boundary(root: Path) -> list[Violation]:
    """Operational authority must not gain browser persistence."""
    source = root / "frontend" / "src"
    violations: list[Violation] = []
    if not source.exists():
        return violations
    for path in source.rglob("*"):
        if path.suffix not in {".ts", ".tsx"} or path.name.endswith((".test.ts", ".test.tsx")):
            continue
        relative = path.relative_to(source).as_posix()
        text = path.read_text(encoding="utf-8")
        facts = project_typescript(text)
        if relative != "app/preferences.ts" and any(
            facts.uses(authority)
            for authority in ("localStorage", "sessionStorage", "indexedDB", "IndexedDB")
        ):
            violations.append(
                Violation(
                    path,
                    "operational identity/lifecycle must not gain browser persistence; "
                    "only app/preferences.ts may use browser storage",
                )
            )
    return violations


def _check_mutable_resource_fences(root: Path) -> list[Violation]:
    """Require mutable/destructive public API methods to carry server-owned fence types.

    TypeScript parameter names are deliberately irrelevant; method identity and the
    public parameter type shape are the structural contract.
    """
    violations: list[Violation] = []
    api_client = root / "frontend" / "src" / "domain" / "api" / "ApiClient.ts"
    if not api_client.is_file():
        return violations
    text = api_client.read_text(encoding="utf-8")
    required = {
        "removeLocation": ("string", "string"),
        "cancelQueuedAdmission": ("string", "number"),
        "drainExecutor": ("string", "number", "number"),
        "activateExecutor": ("string", "number", "number"),
        "revokeExecutorTrust": ("string", "number", "number"),
        "restoreExecutorTrust": ("string", "number", "number"),
        "reconcileEffect": ("string", "number"),
        "acknowledgeEffect": ("string", "string", "number"),
    }
    for method, expected_types in required.items():
        if _typescript_method_parameter_types(text, method) != expected_types:
            violations.append(
                Violation(
                    api_client,
                    f"mutable-resource mutation {method} must retain its explicit server-owned concurrency fence type shape",
                )
            )
    return violations
