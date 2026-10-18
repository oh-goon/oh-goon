from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from oh_goon.repository_tooling.architecture.common import Violation
from oh_goon.repository_tooling.architecture.repository_layout import (
    FRONTEND_ROOT,
    FRONTEND_SOURCE_ROOT,
)
from oh_goon.repository_tooling.architecture.typescript_semantics import project_typescript

FORBIDDEN_BROWSER_APIS = (
    "sessionStorage",
    "indexedDB",
    "document.cookie",
    "cookieStore",
    "navigator.sendBeacon",
)


def _source_files() -> Iterator[Path]:
    for path in FRONTEND_SOURCE_ROOT.rglob("*"):
        if path.suffix in {".ts", ".tsx"} and "generated" not in path.parts:
            yield path


def check_frontend_local_only() -> list[Violation]:
    """Prevent ambient telemetry, persistence, and network paths in the local-first UI."""
    violations: list[Violation] = []
    for path in _source_files():
        text = path.read_text(encoding="utf-8")
        facts = project_typescript(text)
        relative = path.relative_to(FRONTEND_SOURCE_ROOT).as_posix()
        for token in FORBIDDEN_BROWSER_APIS:
            if facts.uses(token):
                violations.append(
                    Violation(path, f"unsupported browser persistence/telemetry: {token}")
                )
        if facts.uses("localStorage") and relative != "app/preferences.ts":
            violations.append(Violation(path, "localStorage is owned only by app/preferences.ts"))
        if facts.external_urls:
            violations.append(Violation(path, "frontend source must not contain an external URL"))
        if relative != "app/api/httpTransport.ts" and facts.uses("fetch"):
            violations.append(
                Violation(path, "raw fetch is owned only by app/api/httpTransport.ts")
            )
    return violations


def check_frontend_e2e_fixture_type_integrity() -> list[Violation]:
    """Keep browser fixtures inside generated TypeScript contracts instead of bypassing them."""
    violations: list[Violation] = []
    e2e_root = FRONTEND_ROOT / "e2e"
    if not e2e_root.exists():
        return violations
    for path in e2e_root.rglob("*"):
        if path.suffix not in {".ts", ".tsx"}:
            continue
        if "as unknown as" in path.read_text(encoding="utf-8"):
            violations.append(
                Violation(
                    path,
                    "E2E fixture/test code must satisfy generated contracts directly; double-cast 'as unknown as' is forbidden",
                )
            )
    return violations
