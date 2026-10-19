from __future__ import annotations

import ast
from pathlib import Path

from oh_goon.repository_tooling.architecture.common import Violation, python_ast
from oh_goon.repository_tooling.architecture.repository_layout import BACKEND_PACKAGE_ROOT

_SELECTION = BACKEND_PACKAGE_ROOT / "integrations/hashmarks/verification_selection.py"
_SELECTION_PRIMITIVES = (
    BACKEND_PACKAGE_ROOT / "integrations/hashmarks/verification_selection_primitives.py"
)
_BUILDER = BACKEND_PACKAGE_ROOT / "repository_tooling/hashmarks_verification_execution.py"
_SHARD_MODEL = BACKEND_PACKAGE_ROOT / "runtime/execution/shard_models.py"
_PYTEST_MEMBER = BACKEND_PACKAGE_ROOT / "core/models/pytest_selection.py"
_PYTEST_PROJECTOR = BACKEND_PACKAGE_ROOT / "runtime/execution/pytest_projection.py"
_REFINEMENT = BACKEND_PACKAGE_ROOT / "runtime/execution/_shard_refinement.py"
_OFFLINE_PROOF = BACKEND_PACKAGE_ROOT / "runtime/execution/offline_proof.py"
_TERMINAL_ATTESTATION = BACKEND_PACKAGE_ROOT / "runtime/execution/terminal_attestation.py"
_AMBIENT = frozenset({"socket", "subprocess", "requests", "httpx", "aiohttp", "urllib", "urllib3"})


def _imports(path: Path) -> set[str]:
    tree = python_ast(path)
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    return imports


def _imported_names(path: Path, module: str) -> set[str]:
    tree = python_ast(path)
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == module:
            result.update(alias.name for alias in node.names)
    return result


def _dataclass_field_sets(path: Path) -> tuple[set[str], ...]:
    tree = python_ast(path)
    field_sets: list[set[str]] = []
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        if not any(
            (isinstance(decorator, ast.Name) and decorator.id == "dataclass")
            or (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Name)
                and decorator.func.id == "dataclass"
            )
            for decorator in node.decorator_list
        ):
            continue
        field_sets.append(
            {
                item.target.id
                for item in node.body
                if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name)
            }
        )
    return tuple(field_sets)


def _assigned_names(path: Path) -> set[str]:
    tree = python_ast(path)
    result: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else (node.target,)
            result.update(target.id for target in targets if isinstance(target, ast.Name))
    return result


def _referenced_external_layout_name(path: Path, name: str) -> bool:
    """Detect consumption of one producer-owned advisory field without source spelling authority."""

    tree = python_ast(path)
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == name:
            return True
        if isinstance(node, ast.Constant) and node.value == name:
            return True
    return False


def _selection_violations() -> list[Violation]:
    violations: list[Violation] = []
    if not _SELECTION.is_file() or not _SELECTION_PRIMITIVES.is_file():
        return [
            Violation(_SELECTION, "Hashmarks verification-selection adapter/primitives are missing")
        ]

    forbidden_native_schema_mirrors = {
        "DOWNSTREAM_CONTRACT_SCHEMA",
        "SELECTION_ENVELOPE_SCHEMA",
        "MEMBER_SCHEMA",
    }
    mirrored = sorted(forbidden_native_schema_mirrors & _assigned_names(_SELECTION_PRIMITIVES))
    if mirrored:
        violations.append(
            Violation(
                _SELECTION_PRIMITIVES,
                "verification-selection adapter must not mirror Hashmarks native schema constants: "
                + ", ".join(mirrored),
            )
        )

    for marker in ("preferred_granularity", "may_regroup", "owner_units", "isolated_process"):
        if any(
            _referenced_external_layout_name(path, marker)
            for path in (_SELECTION, _SELECTION_PRIMITIVES)
        ):
            violations.append(
                Violation(
                    _SELECTION,
                    f"verification execution grouping must not consume Hashmarks layout advisory {marker!r}",
                )
            )

    public_adapter_fields = {
        "producer_implementation_identity",
        "repository_identity",
        "source_identity",
        "repository_provenance_identity",
        "membership_identity",
        "producer_contract_identity",
        "members",
    }
    observed = max(_dataclass_field_sets(_SELECTION), key=len, default=set())
    missing = sorted(public_adapter_fields - observed)
    if missing:
        violations.append(
            Violation(
                _SELECTION,
                "verification-selection adapter is missing structural evidence fields: "
                + ", ".join(missing),
            )
        )

    imported_validator = _imported_names(_SELECTION_PRIMITIVES, "hashmarks")
    if "validate_native_consumer_bundle" not in imported_validator:
        violations.append(
            Violation(
                _SELECTION_PRIMITIVES,
                "verification-selection primitives must consume the Hashmarks-owned consumer validator",
            )
        )

    forbidden = sorted(
        module
        for path in (_SELECTION, _SELECTION_PRIMITIVES)
        for module in _imports(path)
        if module.startswith("oh_goon.runtime") or module in _AMBIENT
    )
    if forbidden:
        violations.append(
            Violation(
                _SELECTION,
                "verification-selection admission must not import runtime/control-plane authority: "
                + ", ".join(forbidden),
            )
        )
    return violations


def _builder_violations() -> list[Violation]:
    if not _BUILDER.is_file():
        return [Violation(_BUILDER, "Oh-Goon verification execution-plan builder is missing")]
    violations: list[Violation] = []
    for marker in ("preferred_granularity", "may_regroup", "owner_units", "isolated_process"):
        if _referenced_external_layout_name(_BUILDER, marker):
            violations.append(
                Violation(
                    _BUILDER,
                    f"verification execution grouping must not consume Hashmarks layout advisory {marker!r}",
                )
            )
    forbidden_imports = {
        "concurrent.futures",
        "subprocess",
        "oh_goon.engine.command",
        "oh_goon.runtime.execution.shards",
    }
    imported = _imports(_BUILDER)
    forbidden = sorted(
        module
        for module in imported
        if module in forbidden_imports
        or any(module.startswith(prefix + ".") for prefix in forbidden_imports)
    )
    if forbidden:
        violations.append(
            Violation(
                _BUILDER,
                "verification plan/receipt builder must not import execution authority: "
                + ", ".join(forbidden),
            )
        )
    return violations


def _shard_model_violations() -> list[Violation]:
    if not _SHARD_MODEL.is_file():
        return [Violation(_SHARD_MODEL, "shard execution evidence model is missing")]
    shared_required = {
        "producer_implementation_identity",
        "producer_contract_identity",
        "verification_selection_evidence_identity",
        "selected_membership_identity",
        "selected_test_nodes",
        "unproven_owner_members",
    }
    plan_required = {
        *shared_required,
        "shards",
    }
    report_required = {
        *shared_required,
        "results",
        "actual_executed_membership_identity",
        "actual_executed_members",
        "membership_complete",
    }
    violations: list[Violation] = []
    field_sets = _dataclass_field_sets(_SHARD_MODEL)
    if not any(plan_required <= fields for fields in field_sets):
        violations.append(
            Violation(
                _SHARD_MODEL,
                "verification execution-plan evidence model is missing required structural fields",
            )
        )
    if not any(report_required <= fields for fields in field_sets):
        violations.append(
            Violation(
                _SHARD_MODEL,
                "verification execution-report evidence model is missing required structural fields",
            )
        )
    return violations


def _structured_pytest_projection_violations() -> list[Violation]:
    violations: list[Violation] = []
    for path in (_PYTEST_MEMBER, _PYTEST_PROJECTOR, _REFINEMENT):
        if not path.is_file():
            violations.append(Violation(path, "structured pytest execution boundary is missing"))
    if violations:
        return violations

    member_required = {"path", "test_symbol", "member_id", "node_components"}
    if not any(member_required <= fields for fields in _dataclass_field_sets(_PYTEST_MEMBER)):
        violations.append(
            Violation(
                _PYTEST_MEMBER,
                "structured pytest member model is missing required execution evidence fields",
            )
        )

    if "oh_goon.core.models.pytest_selection" not in _imports(_PYTEST_PROJECTOR):
        violations.append(
            Violation(
                _PYTEST_PROJECTOR,
                "pytest argv projection must consume the structured pytest model owner rather than reparsing producer strings",
            )
        )
    refinement_imports = _imports(_REFINEMENT)
    for module in (
        "oh_goon.core.models.pytest_selection",
        "oh_goon.runtime.execution.pytest_projection",
    ):
        if module not in refinement_imports:
            violations.append(
                Violation(
                    _REFINEMENT,
                    "shard refinement must retain the structured pytest projection boundary: "
                    + module,
                )
            )
    return violations


def _proof_reuse_non_authority_violations() -> list[Violation]:
    """Keep offline inspection evidence separate from execution admission authority."""

    violations: list[Violation] = []
    for path in (_OFFLINE_PROOF, _TERMINAL_ATTESTATION):
        if not path.is_file():
            violations.append(Violation(path, "offline proof verification owner is missing"))
            continue
        forbidden_imports = {
            "concurrent.futures",
            "subprocess",
            "oh_goon.runtime.execution.shards",
        }
        imported = _imports(path)
        forbidden = sorted(
            module
            for module in imported
            if module in forbidden_imports
            or any(module.startswith(prefix + ".") for prefix in forbidden_imports)
        )
        if forbidden:
            violations.append(
                Violation(
                    path,
                    "offline proof verification must not import execution/reuse authority: "
                    + ", ".join(forbidden),
                )
            )

    offline_import = "oh_goon.runtime.execution.offline_proof"
    terminal_import = "oh_goon.runtime.execution.terminal_attestation"
    for path in sorted(BACKEND_PACKAGE_ROOT.rglob("*.py")):
        if path in {_OFFLINE_PROOF, _TERMINAL_ATTESTATION}:
            continue
        imports = _imports(path)
        if offline_import in imports:
            violations.append(
                Violation(
                    path,
                    "offline shard-proof verification cannot be imported as production execution/admission authority",
                )
            )
        if terminal_import in imports:
            violations.append(
                Violation(
                    path,
                    "terminal-attestation verification cannot be imported as production execution/admission authority without a separate authority design",
                )
            )
    return violations


def check_hashmarks_verification_boundary_contract() -> list[Violation]:
    return [
        *_selection_violations(),
        *_builder_violations(),
        *_shard_model_violations(),
        *_structured_pytest_projection_violations(),
        *_proof_reuse_non_authority_violations(),
    ]
