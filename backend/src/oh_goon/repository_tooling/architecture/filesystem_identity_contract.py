from __future__ import annotations

import ast
from pathlib import Path

from oh_goon.repository_tooling.architecture.common import (
    Violation,
    python_ast,
    python_semantic_facts,
)
from oh_goon.repository_tooling.architecture.repository_layout import ROOT

IDENTITY_MODULES = (
    ROOT / "backend/src/oh_goon/integrations/content_snapshot.py",
    ROOT / "backend/src/oh_goon/integrations/source_snapshot.py",
    ROOT / "backend/src/oh_goon/integrations/content_snapshot_dereferenced.py",
)
ADAPTER = ROOT / "backend/src/oh_goon/integrations/hashmarks/identity.py"


def _hashmarks_public_api_references(path: Path) -> set[str]:
    facts = python_semantic_facts(path)
    references = facts.references
    return {
        public_name
        for public_name in ("RepositoryIdentity", "hash_file")
        if f"hashmarks.{public_name}" in facts.imports
        or f"hashmarks.{public_name}" in references
        or any(reference.startswith(f"hashmarks.{public_name}.") for reference in references)
    }


def _has_verified_observation_call(path: Path) -> bool:
    tree = python_ast(path)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "observe_directory":
            continue
        if any(keyword.arg == "verify" for keyword in node.keywords):
            return True
    return False


def check_filesystem_identity_contract() -> list[Violation]:
    violations: list[Violation] = []
    imported = _hashmarks_public_api_references(ADAPTER)
    for required in ("RepositoryIdentity", "hash_file"):
        if required not in imported:
            violations.append(
                Violation(
                    ADAPTER,
                    f"Hashmarks identity adapter must import public Hashmarks API {required!r}",
                )
            )
    if not _has_verified_observation_call(ADAPTER):
        violations.append(
            Violation(
                ADAPTER,
                "Hashmarks identity adapter must request verified public directory observation",
            )
        )

    for path in IDENTITY_MODULES:
        facts = python_semantic_facts(path)
        if any(
            qualified == "hashlib" or qualified.startswith("hashlib.")
            for qualified, _node in facts.calls
        ):
            violations.append(
                Violation(
                    path,
                    "filesystem content identity must delegate to Hashmarks rather than owning a second SHA tree algorithm",
                )
            )
    return violations
