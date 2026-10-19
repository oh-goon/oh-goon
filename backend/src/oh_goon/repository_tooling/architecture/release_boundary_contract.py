from __future__ import annotations

import json

from oh_goon.repository_tooling.architecture.common import Violation
from oh_goon.repository_tooling.architecture.repository_layout import ROOT


def check_release_boundary_contract() -> list[Violation]:
    """Verify release/product identity that is part of semantic architecture."""
    violations: list[Violation] = []
    version_path = ROOT / "VERSION"
    version = version_path.read_text(encoding="utf-8").strip()
    openapi = json.loads((ROOT / "generated/openapi/openapi.json").read_text(encoding="utf-8"))
    openapi_version = str(openapi.get("info", {}).get("version"))
    if openapi_version != version:
        violations.append(
            Violation(
                version_path,
                "VERSION and generated OpenAPI product version must match; package-manager versions are release metadata, not checkpoint mirrors",
            )
        )
    return violations
