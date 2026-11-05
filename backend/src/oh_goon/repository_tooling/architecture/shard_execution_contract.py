from __future__ import annotations

import ast
from collections.abc import Iterable
from importlib.util import resolve_name
from pathlib import Path

from oh_goon.repository_tooling.architecture.common import (
    Violation,
    python_ast,
    python_semantic_facts,
    python_source_snapshot,
    source_import_spelling,
)
from oh_goon.repository_tooling.architecture.repository_layout import BACKEND_PACKAGE_ROOT

RUNTIME = BACKEND_PACKAGE_ROOT / "runtime/execution/shards.py"
RUNTIME_MODEL_OWNER = BACKEND_PACKAGE_ROOT / "runtime/execution/shard_models.py"
RUNTIME_MODEL = RUNTIME_MODEL_OWNER
SHARD_SCHEMA_SCAN_ROOT = BACKEND_PACKAGE_ROOT
RESUME_OWNER = BACKEND_PACKAGE_ROOT / "runtime/execution/_shard_resume.py"
CANONICAL_MODEL = BACKEND_PACKAGE_ROOT / "core/models/shard_execution.py"
FLEET_EVIDENCE = BACKEND_PACKAGE_ROOT / "core/models/fleet_plan_evidence.py"
GAME_TAPE_MODEL = BACKEND_PACKAGE_ROOT / "core/models/game_tape.py"
GAME_TAPE_PROJECTION = BACKEND_PACKAGE_ROOT / "core/game_tape_projection.py"
DIAGNOSTIC_CLI = BACKEND_PACKAGE_ROOT / "repository_tooling/hashmarks_shard_execution.py"

_SHARD_SCHEMA_CONSTANT_NAMES = (
    "EXECUTION_CONTRACT_VERSION",
    "PASS_EVIDENCE_SCHEMA",
    "REFINEMENT_EVIDENCE_SCHEMA",
)


def _schema_assignments(path: Path) -> dict[str, list[object]]:
    if not path.is_file():
        return {}
    result: dict[str, list[object]] = {}
    for node in python_ast(path).body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else (node.target,)
        value: object = (
            node.value.value
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)
            else object()
        )
        for target in targets:
            if isinstance(target, ast.Name) and target.id in _SHARD_SCHEMA_CONSTANT_NAMES:
                result.setdefault(target.id, []).append(value)
    return result


def _single_owner_schema_values(path: Path) -> dict[str, str] | None:
    assignments = _schema_assignments(path)
    values: dict[str, str] = {}
    for name in _SHARD_SCHEMA_CONSTANT_NAMES:
        observed = assignments.get(name, [])
        if len(observed) != 1 or not isinstance(observed[0], str):
            return None
        values[name] = observed[0]
    return values


def _require_single_schema_owner(violations: list[Violation]) -> None:
    owner_values = _single_owner_schema_values(RUNTIME_MODEL_OWNER)
    if owner_values is None:
        violations.append(
            Violation(
                RUNTIME_MODEL_OWNER,
                "Shard schema owner must assign each schema/version constant exactly once as a string",
            )
        )
        return

    inspected_values = _single_owner_schema_values(RUNTIME_MODEL)
    if inspected_values != owner_values:
        violations.append(
            Violation(
                RUNTIME_MODEL,
                "Shard schema constants must consume the runtime shard-model owner values",
            )
        )

    duplicate_owners: list[str] = []
    if SHARD_SCHEMA_SCAN_ROOT.is_dir():
        for path in SHARD_SCHEMA_SCAN_ROOT.rglob("*.py"):
            if path.resolve() == RUNTIME_MODEL_OWNER.resolve():
                continue
            if _schema_assignments(path):
                duplicate_owners.append(str(path.relative_to(SHARD_SCHEMA_SCAN_ROOT)))
    if duplicate_owners:
        violations.append(
            Violation(
                RUNTIME_MODEL_OWNER,
                "Shard schema constants must have one runtime owner; duplicate assignments: "
                + ", ".join(sorted(duplicate_owners)),
            )
        )


def _class_definition(path: Path, class_name: str) -> ast.ClassDef | None:
    if not path.is_file():
        return None
    matches = [
        node
        for node in python_ast(path).body
        if isinstance(node, ast.ClassDef) and node.name == class_name
    ]
    return matches[0] if len(matches) == 1 else None


def _class_fields(path: Path, class_name: str) -> dict[str, list[ast.AnnAssign]]:
    class_def = _class_definition(path, class_name)
    if class_def is None:
        return {}
    fields: dict[str, list[ast.AnnAssign]] = {}
    for node in class_def.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            fields.setdefault(node.target.id, []).append(node)
    return fields


def _require_class_fields(
    path: Path,
    class_name: str,
    required: Iterable[str],
    violations: list[Violation],
) -> None:
    fields = _class_fields(path, class_name)
    if not fields:
        violations.append(Violation(path, f"Shard contract class {class_name} is missing"))
        return
    for name in required:
        if len(fields.get(name, [])) != 1:
            violations.append(
                Violation(
                    path, f"Shard contract {class_name}.{name} must have exactly one field owner"
                )
            )


def _require_unique_structural_field_owner(
    path: Path,
    required: Iterable[str],
    *,
    label: str,
    violations: list[Violation],
    exact_fields: bool = False,
) -> None:
    required = tuple(required)
    matches: list[str] = []
    if path.is_file():
        for node in python_ast(path).body:
            if not isinstance(node, ast.ClassDef):
                continue
            fields = _class_fields(path, node.name)
            has_required = all(len(fields.get(name, [])) == 1 for name in required)
            if has_required and (not exact_fields or set(fields) == set(required)):
                matches.append(node.name)
    if len(matches) != 1:
        violations.append(
            Violation(
                path,
                f"{label} must have exactly one structural field owner for: " + ", ".join(required),
            )
        )


def _literal_schema_value(path: Path, node: ast.AnnAssign) -> str | None:
    if not isinstance(node.value, ast.Constant) or not isinstance(node.value.value, str):
        return None
    literal_annotations = [
        child
        for child in ast.walk(node.annotation)
        if isinstance(child, ast.Subscript)
        and "typing.Literal" in _annotation_references(path, child.value)
    ]
    if len(literal_annotations) != 1:
        return None
    literals = [
        child.value
        for child in ast.walk(literal_annotations[0].slice)
        if isinstance(child, ast.Constant) and isinstance(child.value, str)
    ]
    if len(literals) != 1 or literals[0] != node.value.value:
        return None
    return node.value.value


def _require_canonical_schema_owner(violations: list[Violation]) -> None:
    fields = _class_fields(CANONICAL_MODEL, "CanonicalShardExecutionEvidence")
    observed = fields.get("contract_schema", [])
    if len(observed) != 1 or _literal_schema_value(CANONICAL_MODEL, observed[0]) is None:
        violations.append(
            Violation(
                CANONICAL_MODEL,
                "CanonicalShardExecutionEvidence.contract_schema must be one fixed model-owned string literal",
            )
        )


def _annotation_references(path: Path, annotation: ast.expr) -> set[str]:
    """Inspect producer-authored type references within this exact source span.

    ``type-syntax`` is the declared scope of this relationship, never a claim
    that an annotation was evaluated or its imported runtime type resolved.
    Other conditions, unknown bindings and unsupported strings stay unproven.
    """
    facts = python_semantic_facts(path, imported_only=True, tree=python_ast(path))
    raw = facts.evidence.source_bytes
    if raw is None:
        raise ValueError("Shard annotation source bytes are unavailable")
    offsets = [0]
    for line in raw.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    if annotation.end_lineno is None or annotation.end_col_offset is None:
        raise ValueError("Shard annotation source range is unavailable")
    start = offsets[annotation.lineno - 1] + annotation.col_offset
    end = offsets[annotation.end_lineno - 1] + annotation.end_col_offset
    return {
        source_import_spelling(target)
        for fact in facts.evidence.packet()["facts"]
        if fact.get("kind") in {"reference", "forward-annotation-reference"}
        and fact.get("qualification") in {"qualified", "conditional"}
        and set(fact.get("conditions", ())) <= {"type-syntax"}
        and not fact.get("reasons")
        and isinstance(target := fact.get("target"), dict)
        and target.get("kind") == "import"
        and start <= fact["range"]["start_byte"]
        and fact["range"]["end_byte"] <= end
    }


def _require_field_type_reference(
    path: Path,
    *,
    class_name: str,
    field_name: str,
    target: str,
    violations: list[Violation],
) -> None:
    fields = _class_fields(path, class_name)
    nodes = fields.get(field_name, [])
    if len(nodes) != 1:
        violations.append(
            Violation(
                path, f"{class_name}.{field_name} must have exactly one structural field owner"
            )
        )
        return
    if target not in _annotation_references(path, nodes[0].annotation):
        violations.append(
            Violation(
                path,
                f"{class_name}.{field_name} must structurally carry canonical shard execution evidence",
            )
        )


def _imports(path: Path, *, package: str) -> set[str]:
    facts = python_semantic_facts(path, imported_only=True, tree=python_ast(path))
    return {
        resolve_name("." * syntax.get("level", 0) + syntax.get("module", ""), package)
        for fact in facts.evidence.packet()["facts"]
        if fact.get("kind") == "import-binding"
        and isinstance(syntax := fact.get("import_syntax"), dict)
    }


def check_shard_execution_contract() -> list[Violation]:
    with python_source_snapshot():
        try:
            return _check_shard_execution_sources()
        except (OSError, SyntaxError, ValueError, ImportError) as error:
            return [
                Violation(
                    BACKEND_PACKAGE_ROOT,
                    f"Shard source observations cannot be inspected: {error}",
                )
            ]


def _check_shard_execution_sources() -> list[Violation]:
    """Protect shard ownership structurally; executable tests own execution choreography."""

    violations: list[Violation] = []
    _require_single_schema_owner(violations)
    _require_canonical_schema_owner(violations)

    _require_unique_structural_field_owner(
        RUNTIME_MODEL,
        (
            "runner_toolchain_identity",
            "dependency_identity",
            "environment_identity",
            "execution_contract_version",
        ),
        label="Shard execution context",
        violations=violations,
        exact_fields=True,
    )
    _require_unique_structural_field_owner(
        RUNTIME_MODEL,
        ("retry_environment_failures",),
        label="Shard retry policy",
        violations=violations,
    )
    _require_class_fields(
        CANONICAL_MODEL,
        "CanonicalShardAttemptEvidence",
        ("attempt_identity", "physical_work_identity"),
        violations,
    )
    _require_class_fields(
        CANONICAL_MODEL,
        "CanonicalShardResultEvidence",
        ("described_work_identity", "physical_work_identity"),
        violations,
    )
    _require_class_fields(
        CANONICAL_MODEL,
        "CanonicalShardExecutionEvidence",
        ("execution_policy_identity", "contract_schema"),
        violations,
    )
    _require_field_type_reference(
        FLEET_EVIDENCE,
        class_name="FleetPlanResult",
        field_name="shard_execution",
        target="oh_goon.core.models.shard_execution.CanonicalShardExecutionEvidence",
        violations=violations,
    )
    _require_class_fields(
        GAME_TAPE_MODEL,
        "GameTapeShardAttemptLineage",
        ("attempt_identity", "physical_work_identity"),
        violations,
    )
    _require_class_fields(
        GAME_TAPE_MODEL,
        "GameTapeShardLineage",
        ("described_work_identity", "physical_work_identity"),
        violations,
    )

    if not DIAGNOSTIC_CLI.is_file():
        violations.append(Violation(DIAGNOSTIC_CLI, "Standalone shard diagnostic CLI is missing"))
    else:
        allowed_runtime = {"oh_goon.runtime.execution.shards"}
        forbidden = sorted(
            module
            for module in _imports(DIAGNOSTIC_CLI, package="oh_goon.repository_tooling")
            if (module.startswith("oh_goon.runtime.") and module not in allowed_runtime)
            or module.startswith("oh_goon.services.")
            or module.startswith("oh_goon.core.models.run")
            or module.startswith("oh_goon.core.models.schedule")
        )
        if forbidden:
            violations.append(
                Violation(
                    DIAGNOSTIC_CLI,
                    "Standalone shard CLI is diagnostic and must not become a second canonical Run owner: "
                    + ", ".join(forbidden),
                )
            )
    return violations
