"""Prevent unqualified or duplicated M146 semantic-owner claims in docs."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OWNER = ROOT / "backend/src/oh_goon/core/enforcer_lace_semantics.py"


def test_semantic_purity_no_live_runtime_imports_or_side_effect_apis() -> None:
    import ast

    tree = ast.parse(OWNER.read_text(encoding="utf-8"))
    imports = [
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    ]
    assert all(
        module in {"__future__", "dataclasses", "enum", "types", "typing"}
        for module in imports
    )
    assert not any(isinstance(node, ast.Import) for node in ast.walk(tree))
    symbols = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
    assert {
        "verify_committed_prefix",
        "envelope_at",
        "evaluate",
        "derive_base_execution_result",
        "classify_step_outcome",
        "rollup_run",
    } <= symbols
